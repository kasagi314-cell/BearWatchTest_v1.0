package com.bearwatch.probe

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 72 時間の常駐耐久テスト用 Foreground Service。
 *
 * 1 分ごとにタイムスタンプ・バッテリー残量・CPU 温度を記録し、
 * JSONL ファイルに追記する。電源断からの自動復帰も確認対象。
 */
class EnduranceService : Service() {

    companion object {
        const val CHANNEL_ID = "probe_endurance"
        const val NOTIFICATION_ID = 1
        const val LOG_INTERVAL_MS = 60_000L  // 1 分
        const val ACTION_STOP = "com.bearwatch.probe.STOP_ENDURANCE"
        const val ACTION_TICK = "com.bearwatch.probe.ENDURANCE_TICK"

        @Volatile
        var isRunning = false
            private set

        fun start(context: Context) {
            val intent = Intent(context, EnduranceService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, EnduranceService::class.java))
        }

        fun getLogFile(context: Context): File {
            return File(context.getExternalFilesDir(null), "endurance_log.jsonl")
        }
    }

    private var wakeLock: PowerManager.WakeLock? = null
    private var startTimeMs = 0L
    private var running = false

    private val tickReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (running) {
                writeLogEntry()
                scheduleNextTick()
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }

        // START_STICKY による再起動や二重呼び出しを防ぐ
        if (running) {
            return START_STICKY
        }
        running = true
        isRunning = true

        startTimeMs = System.currentTimeMillis()

        val notification = buildNotification("耐久テスト実行中")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        // 既存の WakeLock があれば先に解放する
        wakeLock?.let { if (it.isHeld) it.release() }

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "bearwatch:endurance"
        ).apply {
            // 73 時間でタイムアウト（72h テスト + 余裕 1h）。
            // リークしても OS が自動解放する。
            acquire(73 * 3_600_000L)
        }

        // BroadcastReceiver を登録
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(tickReceiver, IntentFilter(ACTION_TICK),
                Context.RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(tickReceiver, IntentFilter(ACTION_TICK))
        }

        // 初回ログ（再起動時は event: restarted を記録する）
        val isRestart = intent == null  // START_STICKY 再起動では intent が null
        writeLogEntry(event = if (isRestart) "restarted" else "started")
        scheduleNextTick()

        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        isRunning = false
        cancelNextTick()
        try { unregisterReceiver(tickReceiver) } catch (_: Exception) {}
        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        writeLogEntry(event = "stopped")
        super.onDestroy()
    }

    private fun scheduleNextTick() {
        val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = PendingIntent.getBroadcast(
            this, 0, Intent(ACTION_TICK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val triggerAt = SystemClock.elapsedRealtime() + LOG_INTERVAL_MS
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, intent)
        } else {
            am.setExact(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, intent)
        }
    }

    private fun cancelNextTick() {
        val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = PendingIntent.getBroadcast(
            this, 0, Intent(ACTION_TICK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        am.cancel(intent)
    }

    private fun writeLogEntry(event: String? = null) {
        try {
            val now = System.currentTimeMillis()
            val utcFmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }

            val batteryPct = getBatteryPercent()
            val batteryTempC = getBatteryTemperature()
            val entry = JSONObject().apply {
                put("timestamp_utc", utcFmt.format(Date(now)))
                put("uptime_min", (now - startTimeMs) / 60_000)
                put("battery_pct", batteryPct)
                put("battery_temp_c", batteryTempC)
                if (event != null) put("event", event)
            }

            val line = entry.toString() + "\n"
            val file = getLogFile(this)
            // FileOutputStream で書き込み後に sync し、
            // 電源断でも直前までのエントリを保証する
            java.io.FileOutputStream(file, true).use { fos ->
                fos.write(line.toByteArray())
                fos.fd.sync()
            }

            // 通知を更新
            val elapsed = (now - startTimeMs) / 3_600_000.0
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(NOTIFICATION_ID,
                buildNotification("耐久テスト: %.1fh経過 / バッテリー%d%%".format(elapsed, batteryPct)))
        } catch (_: Exception) {
            // ログ書き込み失敗で Service を落とさない
        }
    }

    private fun getBatteryPercent(): Int {
        val intent = registerReceiver(null,
            android.content.IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = intent?.getIntExtra(android.os.BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = intent?.getIntExtra(android.os.BatteryManager.EXTRA_SCALE, 100) ?: 100
        return if (scale > 0) (level * 100) / scale else -1
    }

    /**
     * バッテリー温度を返す（°C）。
     *
     * CPU 温度（/sys/class/thermal/）は SELinux により untrusted_app から
     * アクセス拒否され、TSENS のデフォルト値 125 が返るため使えない。
     * BatteryManager.EXTRA_TEMPERATURE は制限なく取得可能。
     * 単位は 1/10°C（例: 310 = 31.0°C）。
     *
     * バッテリー温度は CPU より 10〜20°C 低い傾向がある。
     * 筐体内の過熱シャットダウン閾値はこの差を考慮して設定すること。
     */
    private fun getBatteryTemperature(): Double {
        val intent = registerReceiver(null,
            android.content.IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val tenths = intent?.getIntExtra(android.os.BatteryManager.EXTRA_TEMPERATURE, -1) ?: -1
        return if (tenths > 0) tenths / 10.0 else -1.0
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "耐久テスト",
                NotificationManager.IMPORTANCE_LOW
            )
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(text: String): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle("BearWatch Probe")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setOngoing(true)
            .build()
    }
}

package com.bearwatch.probe

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * M0: 端末の素性を確認するためのメイン画面。
 *
 * [Camera Probe]   §3 の表を埋める
 * [Benchmark]      カメラ切替・背景差分・TFLite 推論の所要時間
 * [Endurance 72h]  常駐耐久テストの開始/停止
 * [Export JSON]    全結果を JSON ファイルに書き出す
 */
class MainActivity : AppCompatActivity() {

    private lateinit var tvOutput: TextView
    private val results = JSONObject()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvOutput = findViewById(R.id.tv_output)
        val btnProbe: Button = findViewById(R.id.btn_probe)
        val btnBenchmark: Button = findViewById(R.id.btn_benchmark)
        val btnEndurance: Button = findViewById(R.id.btn_endurance)
        val btnExport: Button = findViewById(R.id.btn_export)

        requestPermissions()

        btnProbe.setOnClickListener { runCameraProbe() }
        btnBenchmark.setOnClickListener { runBenchmark() }
        btnEndurance.setOnClickListener { toggleEndurance() }
        btnExport.setOnClickListener { exportJson() }

        // Activity が再生成された場合、Service の実態に合わせてボタンを更新
        if (isEnduranceRunning()) {
            btnEndurance.text = "Stop Endurance"
        }

        requestBatteryOptimizationExemption()
    }

    private fun requestPermissions() {
        val perms = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED) {
            perms.add(Manifest.permission.CAMERA)
        }
        // WRITE_EXTERNAL_STORAGE は API 29+ で不要（getExternalFilesDir はパーミッション不要）
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            }
        }
        // API 33+ では通知パーミッションが必要
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (perms.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, perms.toTypedArray(), 100)
        }
    }

    private fun requestBatteryOptimizationExemption() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = getSystemService(POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                }
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    appendOutput("バッテリー最適化の除外を手動で設定してください")
                }
            }
        }
    }

    private fun runCameraProbe() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED) {
            appendOutput("カメラ権限がありません。許可してから再実行してください。")
            return
        }

        try {
            val probe = CameraProbe(this)
            val result = probe.probe()
            results.put("camera", result.json)
            setOutput(result.display)
        } catch (e: Exception) {
            appendOutput("Camera Probe エラー: ${e.message}")
        }
    }

    private fun runBenchmark() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED) {
            appendOutput("カメラ権限がありません。許可してから再実行してください。")
            return
        }

        val btnBenchmark: Button = findViewById(R.id.btn_benchmark)
        btnBenchmark.isEnabled = false
        setOutput("ベンチマーク実行中...\n")

        Thread {
            val runner = BenchmarkRunner(this)
            val benchJson = JSONObject()
            val sb = StringBuilder()
            sb.appendLine("=== Benchmark ===\n")

            // 1. 背景差分（軽量。まず確実に結果を取る）
            runOnUiThread { appendOutput("背景差分を計測中...") }
            try {
                val r = runner.runBackgroundDiff()
                benchJson.put("background_diff", r)
                sb.appendLine("--- Background Diff 1280x720 (100 trials) ---")
                sb.appendLine("  Median: ${r.optString("median_ms", "N/A")} ms")
                sb.appendLine("  Pass:   ${r.optBoolean("pass", false)} (≤ 50 ms)\n")
            } catch (e: Exception) {
                sb.appendLine("--- Background Diff: エラー ${e.message} ---\n")
            }
            saveBenchmarkProgress(benchJson)
            runOnUiThread { setOutput(sb.toString()) }

            // 2. カメラ切替
            runOnUiThread { appendOutput("カメラ切替を計測中...") }
            try {
                val r = runner.runCameraSwitch()
                benchJson.put("camera_switch", r)
                sb.appendLine("--- Camera Switch (10 trials) ---")
                sb.appendLine("  Median: ${r.optString("median_ms", "N/A")} ms")
                sb.appendLine("  Pass:   ${r.optBoolean("pass", false)} (≤ 2000 ms)\n")
            } catch (e: Exception) {
                sb.appendLine("--- Camera Switch: エラー ${e.message} ---\n")
            }
            saveBenchmarkProgress(benchJson)
            runOnUiThread { setOutput(sb.toString()) }

            // GC してカメラのヒープを回収してから TFLite に入る
            System.gc()
            Thread.sleep(1000)

            // 3. TFLite（ネイティブクラッシュの可能性あり。最後に実行）
            runOnUiThread { appendOutput("TFLite 推論を計測中...\n※ ここでアプリが落ちた場合、\n  前2つの結果は benchmark_partial.json に保存済みです") }
            try {
                val r = runner.runTfliteInference()
                benchJson.put("tflite_inference", r)
                sb.appendLine("--- TFLite 96x96 Inference (50 trials) ---")
                sb.appendLine("  Median: ${r.optString("median_ms", "N/A")} ms")
                sb.appendLine("  Pass:   ${r.optBoolean("pass", false)} (≤ 200 ms)\n")
            } catch (e: Exception) {
                sb.appendLine("--- TFLite: エラー ${e.message} ---\n")
            }

            results.put("benchmark", benchJson)
            saveBenchmarkProgress(benchJson)
            runOnUiThread {
                setOutput(sb.toString())
                btnBenchmark.isEnabled = true
            }
        }.start()
    }

    /** 各テスト完了時に途中結果を保存する。TFLite でクラッシュしても前の結果が残る */
    private fun saveBenchmarkProgress(benchJson: JSONObject) {
        try {
            val dir = getExternalFilesDir(null) ?: filesDir
            val file = File(dir, "benchmark_partial.json")
            file.writeText(benchJson.toString(2))
        } catch (_: Exception) {
            // 保存失敗は握りつぶす
        }
    }

    private fun isEnduranceRunning(): Boolean {
        return EnduranceService.isRunning
    }

    private fun toggleEndurance() {
        val btnEndurance: Button = findViewById(R.id.btn_endurance)
        // Activity が再生成されてもサービスの実態と同期する
        val enduranceRunning = isEnduranceRunning()
        if (!enduranceRunning) {
            EnduranceService.start(this)
            btnEndurance.text = "Stop Endurance"
            appendOutput("\n耐久テスト開始。通知バーで経過を確認できます。\n" +
                "ログ: ${EnduranceService.getLogFile(this).absolutePath}\n")
        } else {
            EnduranceService.stop(this)
            btnEndurance.text = "Endurance 72h"
            appendOutput("\n耐久テスト停止。\n")

            // ログのサマリーを表示
            val logFile = EnduranceService.getLogFile(this)
            if (logFile.exists()) {
                val lines = logFile.readLines()
                appendOutput("記録エントリ数: ${lines.size}\n")
                if (lines.isNotEmpty()) {
                    appendOutput("最終エントリ: ${lines.last()}\n")
                }
            }
        }
    }

    private fun exportJson() {
        if (results.length() == 0) {
            appendOutput("\nまだ結果がありません。先に Camera Probe か Benchmark を実行してください。\n")
            return
        }

        val utcFmt = SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
        val filename = "probe_${utcFmt.format(Date())}.json"

        val dir = getExternalFilesDir(null) ?: filesDir
        val file = File(dir, filename)
        file.writeText(results.toString(2))

        appendOutput("\nJSON を保存しました:\n${file.absolutePath}\n")

        // 耐久テストのログファイルも案内
        val enduranceLog = EnduranceService.getLogFile(this)
        if (enduranceLog.exists()) {
            appendOutput("耐久ログ:\n${enduranceLog.absolutePath}\n")
        }

        Toast.makeText(this, "Saved: $filename", Toast.LENGTH_LONG).show()
    }

    private fun setOutput(text: String) {
        tvOutput.text = text
    }

    private fun appendOutput(text: String) {
        tvOutput.append(text + "\n")
    }
}

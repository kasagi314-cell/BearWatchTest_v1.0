package com.bearwatch.probe

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * BOOT_COMPLETED を受けて EnduranceService を再起動する。
 *
 * 前提条件:
 * - ユーザーが一度でもアプリを手動起動していること（Android 3.1+ の停止状態制約）
 * - ユーザーが「強制停止」していないこと
 *
 * EnduranceService.start() 内で API 24-25 は startService()、
 * API 26+ は startForegroundService() を使い分け済み。
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            EnduranceService.start(context)
        }
    }
}

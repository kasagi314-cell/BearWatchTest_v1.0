package com.bearwatch.probe

import android.content.Context
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject

/**
 * Camera2 API でカメラの素性を取得する。
 * SPEC.md §3 の表を埋めるためのもの。
 */
class CameraProbe(private val context: Context) {

    fun probe(): ProbeResult {
        val cm = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val cameras = JSONArray()

        for (id in cm.cameraIdList) {
            val chars = cm.getCameraCharacteristics(id)
            cameras.put(probeCamera(id, chars))
        }

        val device = JSONObject().apply {
            put("model", Build.MODEL)
            put("manufacturer", Build.MANUFACTURER)
            put("device", Build.DEVICE)
            put("sdk_int", Build.VERSION.SDK_INT)
            put("release", Build.VERSION.RELEASE)
            put("abis", JSONArray(Build.SUPPORTED_ABIS.toList()))
        }

        val json = JSONObject().apply {
            put("device", device)
            put("cameras", cameras)
        }

        return ProbeResult(json, formatForDisplay(json))
    }

    private fun probeCamera(id: String, chars: CameraCharacteristics): JSONObject {
        val facing = when (chars.get(CameraCharacteristics.LENS_FACING)) {
            CameraCharacteristics.LENS_FACING_BACK -> "rear"
            CameraCharacteristics.LENS_FACING_FRONT -> "front"
            else -> "external"
        }

        val hwLevel = chars.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)
        val hwLevelName = when (hwLevel) {
            CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LEGACY -> "LEGACY"
            CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED -> "LIMITED"
            CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_FULL -> "FULL"
            CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_3 -> "LEVEL_3"
            else -> "UNKNOWN($hwLevel)"
        }

        // 露出ロック
        val aeLockAvailable = chars.get(CameraCharacteristics.CONTROL_AE_LOCK_AVAILABLE) ?: false

        // AF モード
        val afModes = chars.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES) ?: intArrayOf()
        val afModeNames = afModes.map { mode ->
            when (mode) {
                CameraCharacteristics.CONTROL_AF_MODE_OFF -> "OFF"
                CameraCharacteristics.CONTROL_AF_MODE_AUTO -> "AUTO"
                CameraCharacteristics.CONTROL_AF_MODE_MACRO -> "MACRO"
                CameraCharacteristics.CONTROL_AF_MODE_CONTINUOUS_VIDEO -> "CONTINUOUS_VIDEO"
                CameraCharacteristics.CONTROL_AF_MODE_CONTINUOUS_PICTURE -> "CONTINUOUS_PICTURE"
                CameraCharacteristics.CONTROL_AF_MODE_EDOF -> "EDOF"
                else -> "UNKNOWN($mode)"
            }
        }
        // AF_MODE_OFF があれば無限遠固定が可能（マニュアルフォーカス）
        val infinityFocusPossible = afModes.contains(CameraCharacteristics.CONTROL_AF_MODE_OFF)

        // 解像度
        val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)

        val previewSizes = map?.getOutputSizes(SurfaceTexture::class.java)
            ?.sortedByDescending { it.width * it.height }
            ?: emptyList()

        val stillSizes = map?.getOutputSizes(ImageFormat.JPEG)
            ?.sortedByDescending { it.width * it.height }
            ?: emptyList()

        // 焦点距離（画角計算の手がかり）
        val focalLengths = chars.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
            ?: floatArrayOf()
        val sensorSize = chars.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)

        // センサーサイズと焦点距離から画角を推定
        var estimatedHfovDeg: Double? = null
        if (focalLengths.isNotEmpty() && sensorSize != null) {
            val focalMm = focalLengths[0]
            val sensorWidthMm = sensorSize.width
            estimatedHfovDeg = Math.toDegrees(
                2.0 * Math.atan((sensorWidthMm / (2.0 * focalMm)).toDouble())
            )
        }

        return JSONObject().apply {
            put("id", id)
            put("facing", facing)
            put("hardware_level", hwLevelName)
            put("ae_lock_available", aeLockAvailable)
            put("af_modes", JSONArray(afModeNames))
            put("infinity_focus_possible", infinityFocusPossible)
            put("preview_sizes", JSONArray().apply {
                previewSizes.take(5).forEach { s ->
                    put("${s.width}x${s.height}")
                }
            })
            put("preview_max", previewSizes.firstOrNull()?.let { "${it.width}x${it.height}" } ?: "N/A")
            put("still_sizes", JSONArray().apply {
                stillSizes.take(5).forEach { s ->
                    put("${s.width}x${s.height}")
                }
            })
            put("still_max", stillSizes.firstOrNull()?.let { "${it.width}x${it.height}" } ?: "N/A")
            put("focal_lengths_mm", JSONArray(focalLengths.toList()))
            if (sensorSize != null) {
                put("sensor_size_mm", "${sensorSize.width}x${sensorSize.height}")
            }
            if (estimatedHfovDeg != null) {
                put("estimated_hfov_deg", String.format("%.1f", estimatedHfovDeg))
            }
        }
    }

    private fun formatForDisplay(json: JSONObject): String {
        val sb = StringBuilder()

        val dev = json.getJSONObject("device")
        sb.appendLine("=== Device ===")
        sb.appendLine("Model:   ${dev.getString("manufacturer")} ${dev.getString("model")}")
        sb.appendLine("Android: ${dev.getString("release")} (SDK ${dev.getInt("sdk_int")})")
        sb.appendLine("ABIs:    ${dev.getJSONArray("abis")}")
        sb.appendLine()

        val cameras = json.getJSONArray("cameras")
        for (i in 0 until cameras.length()) {
            val cam = cameras.getJSONObject(i)
            val facing = cam.getString("facing")
            sb.appendLine("=== Camera $i ($facing) ===")
            sb.appendLine("HW Level:       ${cam.getString("hardware_level")}")
            sb.appendLine("AE Lock:        ${cam.getBoolean("ae_lock_available")}")
            sb.appendLine("AF Modes:       ${cam.getJSONArray("af_modes")}")
            sb.appendLine("Infinity Focus: ${cam.getBoolean("infinity_focus_possible")}")
            sb.appendLine("Preview Max:    ${cam.getString("preview_max")}")
            sb.appendLine("Still Max:      ${cam.getString("still_max")}")
            if (cam.has("estimated_hfov_deg")) {
                sb.appendLine("Est. HFOV:      ${cam.getString("estimated_hfov_deg")}° (要実測)")
            }
            if (cam.has("sensor_size_mm")) {
                sb.appendLine("Sensor Size:    ${cam.getString("sensor_size_mm")} mm")
            }
            sb.appendLine()
        }

        return sb.toString()
    }

    data class ProbeResult(val json: JSONObject, val display: String)
}

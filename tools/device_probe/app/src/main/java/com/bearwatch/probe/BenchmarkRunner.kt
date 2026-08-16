package com.bearwatch.probe

import android.content.Context
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.view.Surface
import org.json.JSONObject
import org.tensorflow.lite.Interpreter
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * SPEC.md §3 の性能計測項目を個別に実行する。
 *
 * 2GB 端末ではカメラ HAL と TFLite を同一プロセスで同時に動かすと
 * LMK に殺されるため、各テストは独立して呼び出す設計にしている。
 */
class BenchmarkRunner(private val context: Context) {

    /** 背景差分（カメラ不使用・メモリ軽量。最初に実行して問題ない） */
    fun runBackgroundDiff(): JSONObject {
        val w = 1280
        val h = 720
        val size = w * h

        val bg = ByteArray(size) { (it % 256).toByte() }
        val fg = ByteArray(size) { ((it + 30) % 256).toByte() }
        val diff = ByteArray(size)
        val threshold: Byte = 25

        repeat(5) { absDiffThreshold(bg, fg, diff, threshold) }

        val trials = 100
        val times = LongArray(trials)
        for (i in 0 until trials) {
            val start = System.nanoTime()
            absDiffThreshold(bg, fg, diff, threshold)
            times[i] = (System.nanoTime() - start) / 1_000_000
        }

        val sorted = times.sorted()
        val median = sorted[sorted.size / 2]

        return JSONObject().apply {
            put("resolution", "${w}x${h}")
            put("trials", trials)
            put("median_ms", median)
            put("min_ms", sorted.first())
            put("max_ms", sorted.last())
            put("pass", median <= 50)
        }
    }

    /** カメラ切替（ネイティブメモリを大量消費する） */
    fun runCameraSwitch(): JSONObject {
        val thread = HandlerThread("benchmark-camera").apply { start() }
        val handler = Handler(thread.looper)

        try {
            return measureCameraSwitch(handler)
        } finally {
            thread.quitSafely()
        }
    }

    /** TFLite 推論（カメラと同時に使わないこと） */
    fun runTfliteInference(): JSONObject {
        // Step 0: ネイティブライブラリのロード確認
        try {
            System.loadLibrary("tensorflowlite_jni")
        } catch (e: UnsatisfiedLinkError) {
            return JSONObject().apply {
                put("error", "ネイティブライブラリ読み込み失敗: ${e.message}")
                put("phase", "native_load")
                put("pass", false)
            }
        } catch (e: Exception) {
            return JSONObject().apply {
                put("error", "ライブラリ初期化失敗: ${e.message}")
                put("phase", "native_load")
                put("pass", false)
            }
        }

        val modelFile: File
        try {
            modelFile = File(context.cacheDir, "dummy_model.tflite")
            if (!modelFile.exists()) {
                context.assets.open("dummy_model.tflite").use { input ->
                    modelFile.outputStream().use { output -> input.copyTo(output) }
                }
            }
        } catch (e: Exception) {
            return JSONObject().apply {
                put("error", "モデル読み込み失敗: ${e.message}")
                put("phase", "model_load")
                put("pass", false)
            }
        }

        val delegate = "CPU_NO_XNNPACK"
        val threads = 2
        val interpreter: Interpreter
        try {
            val opts = Interpreter.Options().apply {
                setNumThreads(threads)
                setUseXNNPACK(false)
            }
            interpreter = Interpreter(modelFile, opts)
        } catch (e: Exception) {
            return JSONObject().apply {
                put("error", "TFLite Interpreter 初期化失敗: ${e.message}")
                put("pass", false)
            }
        }

        try {
            val inputBuffer = ByteBuffer.allocateDirect(96 * 96 * 3 * 4).order(ByteOrder.nativeOrder())
            val outputBuffer = ByteBuffer.allocateDirect(2 * 4).order(ByteOrder.nativeOrder())

            repeat(3) {
                inputBuffer.rewind()
                outputBuffer.rewind()
                interpreter!!.run(inputBuffer, outputBuffer)
            }

            val trials = 50
            val times = LongArray(trials)
            for (i in 0 until trials) {
                inputBuffer.rewind()
                outputBuffer.rewind()
                val start = System.nanoTime()
                interpreter!!.run(inputBuffer, outputBuffer)
                times[i] = (System.nanoTime() - start) / 1_000_000
            }

            val sorted = times.sorted()
            val median = sorted[sorted.size / 2]

            return JSONObject().apply {
                put("input_shape", "96x96x3")
                put("output_classes", 2)
                put("delegate", delegate)
                put("threads", threads)
                put("trials", trials)
                put("median_ms", median)
                put("min_ms", sorted.first())
                put("max_ms", sorted.last())
                put("pass", median <= 200)
                put("note", "ダミーモデル（単層）。実モデルはこれより遅い")
            }
        } catch (e: Exception) {
            return JSONObject().apply {
                put("error", "TFLite 推論中にクラッシュ: ${e.message}")
                put("delegate", delegate)
                put("pass", false)
            }
        } finally {
            interpreter?.close()
        }
    }

    // --- private helpers ---

    private fun absDiffThreshold(a: ByteArray, b: ByteArray, out: ByteArray, thresh: Byte) {
        val t = thresh.toInt() and 0xFF
        for (i in a.indices) {
            val va = a[i].toInt() and 0xFF
            val vb = b[i].toInt() and 0xFF
            val d = if (va > vb) va - vb else vb - va
            out[i] = if (d > t) 0xFF.toByte() else 0
        }
    }

    @Suppress("MissingPermission")
    private fun measureCameraSwitch(handler: Handler): JSONObject {
        val cm = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val ids = cm.cameraIdList
        if (ids.size < 2) {
            return JSONObject().apply {
                put("error", "カメラが2台未満")
                put("pass", false)
            }
        }

        var rearId: String? = null
        var frontId: String? = null
        for (id in ids) {
            val facing = cm.getCameraCharacteristics(id)
                .get(CameraCharacteristics.LENS_FACING)
            when (facing) {
                CameraCharacteristics.LENS_FACING_BACK -> rearId = id
                CameraCharacteristics.LENS_FACING_FRONT -> frontId = id
            }
        }
        if (rearId == null || frontId == null) {
            return JSONObject().apply {
                put("error", "前面または背面カメラが見つからない")
                put("pass", false)
            }
        }

        val times = mutableListOf<Long>()
        val trials = 10

        openAndGetFirstFrame(cm, rearId, handler)

        for (i in 0 until trials) {
            val fromId = if (i % 2 == 0) rearId else frontId
            val start = System.nanoTime()
            openAndGetFirstFrame(cm, fromId, handler)
            val elapsed = (System.nanoTime() - start) / 1_000_000
            times.add(elapsed)
        }

        val sorted = times.sorted()
        val median = sorted[sorted.size / 2]

        return JSONObject().apply {
            put("trials", trials)
            put("median_ms", median)
            put("min_ms", sorted.first())
            put("max_ms", sorted.last())
            put("pass", median <= 2000)
        }
    }

    private fun openAndGetFirstFrame(cm: CameraManager, cameraId: String, handler: Handler) {
        val latch = CountDownLatch(1)
        var device: CameraDevice? = null
        var activeSession: CameraCaptureSession? = null

        val surfaceTexture = SurfaceTexture(0)
        surfaceTexture.setDefaultBufferSize(640, 480)
        val surface = Surface(surfaceTexture)

        cm.openCamera(cameraId, object : CameraDevice.StateCallback() {
            override fun onOpened(cam: CameraDevice) {
                device = cam
                val req = cam.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
                req.addTarget(surface)

                val sessionCallback = object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(session: CameraCaptureSession) {
                        activeSession = session
                        session.setRepeatingRequest(req.build(), null, handler)
                        surfaceTexture.setOnFrameAvailableListener {
                            try { session.stopRepeating() } catch (_: Exception) {}
                            try { session.close() } catch (_: Exception) {}
                            latch.countDown()
                        }
                    }
                    override fun onConfigureFailed(session: CameraCaptureSession) {
                        latch.countDown()
                    }
                }

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    val outputConfig = OutputConfiguration(surface)
                    val sessionConfig = SessionConfiguration(
                        SessionConfiguration.SESSION_REGULAR,
                        listOf(outputConfig),
                        context.mainExecutor,
                        sessionCallback
                    )
                    cam.createCaptureSession(sessionConfig)
                } else {
                    @Suppress("DEPRECATION")
                    cam.createCaptureSession(listOf(surface), sessionCallback, handler)
                }
            }
            override fun onDisconnected(cam: CameraDevice) { cam.close(); latch.countDown() }
            override fun onError(cam: CameraDevice, error: Int) { cam.close(); latch.countDown() }
        }, handler)

        latch.await(5, TimeUnit.SECONDS)
        try { activeSession?.stopRepeating() } catch (_: Exception) {}
        try { activeSession?.close() } catch (_: Exception) {}
        device?.close()
        surface.release()
        surfaceTexture.release()
    }
}

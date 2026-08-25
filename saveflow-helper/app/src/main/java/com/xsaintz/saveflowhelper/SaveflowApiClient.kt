package com.xsaintz.saveflowhelper

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

class SaveflowApiClient(
    private val apiRoot: String = "https://saveflow-ten.vercel.app"
) {
    fun extract(sourceUrl: String): List<MediaCandidate> {
        val connection = URL("$apiRoot/api/extract").openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 20_000
            connection.readTimeout = 60_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.bufferedWriter(StandardCharsets.UTF_8).use {
                it.write(JSONObject().put("url", sourceUrl).toString())
            }

            val responseCode = connection.responseCode
            val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (responseCode !in 200..299) {
                val detail = runCatching { JSONObject(body).optString("detail") }.getOrNull()
                throw IllegalStateException(detail?.takeIf { it.isNotBlank() } ?: "Saveflow API error $responseCode")
            }
            parseCandidates(JSONObject(body), sourceUrl)
        } finally {
            connection.disconnect()
        }
    }

    internal fun parseCandidates(response: JSONObject, sourceUrl: String): List<MediaCandidate> {
        val candidates = mutableListOf<MediaCandidate>()
        val items = response.optJSONArray("items") ?: return emptyList()

        for (itemPosition in 0 until items.length()) {
            val item = items.optJSONObject(itemPosition) ?: continue
            val index = item.optInt("index", itemPosition)
            val title = item.optString("title", "media")
            val thumbnail = item.optString("thumbnail").takeIf { it.isNotBlank() }
            val formats = item.optJSONArray("formats")

            if (formats != null) {
                for (formatPosition in 0 until formats.length()) {
                    val format = formats.optJSONObject(formatPosition) ?: continue
                    val kind = format.optString("kind")
                    val type = when (kind) {
                        "image" -> MediaType.PHOTO
                        "video", "stream" -> MediaType.VIDEO
                        else -> continue
                    }
                    val formatId = format.optString("format_id")
                    val extension = format.optString("ext").ifBlank {
                        if (type == MediaType.PHOTO) "jpg" else "mp4"
                    }
                    val label = format.optString("label")
                    candidates += MediaCandidate(
                        url = downloadUrl(sourceUrl, index, formatId),
                        type = type,
                        source = "SAVEFLOW API${label.takeIf { it.isNotBlank() }?.let { " - $it" }.orEmpty()}",
                        referer = sourceUrl,
                        poster = thumbnail?.let { thumbnailUrl(sourceUrl, index) },
                        mimeType = if (type == MediaType.PHOTO) "image/$extension" else "video/$extension",
                        filename = safeFilename(title, label, extension)
                    )
                }
            }

            if ((formats == null || formats.length() == 0) && thumbnail != null) {
                candidates += MediaCandidate(
                    url = thumbnailUrl(sourceUrl, index),
                    type = MediaType.PHOTO,
                    source = "SAVEFLOW API - Preview",
                    referer = sourceUrl,
                    poster = thumbnailUrl(sourceUrl, index),
                    mimeType = "image/jpeg",
                    filename = safeFilename(title, "preview", "jpg")
                )
            }
        }
        return candidates
    }

    private fun downloadUrl(sourceUrl: String, index: Int, formatId: String): String =
        "$apiRoot/api/download?url=${encode(sourceUrl)}&index=$index&format_id=${encode(formatId)}"

    private fun thumbnailUrl(sourceUrl: String, index: Int): String =
        "$apiRoot/api/thumbnail?url=${encode(sourceUrl)}&index=$index"

    private fun encode(value: String): String =
        URLEncoder.encode(value, StandardCharsets.UTF_8.name())

    private fun safeFilename(title: String, label: String, extension: String): String {
        val stem = listOf(title, label)
            .filter { it.isNotBlank() }
            .joinToString("_")
            .replace(Regex("[^A-Za-z0-9._ -]+"), "_")
            .trim(' ', '.', '_')
            .take(80)
            .ifBlank { "media" }
        val ext = extension.replace(Regex("[^A-Za-z0-9]"), "").ifBlank { "bin" }
        return "$stem.$ext"
    }
}

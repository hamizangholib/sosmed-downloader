package com.xsaintz.saveflowhelper

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

class MediaDetector(private val onCandidatesUpdated: (List<MediaCandidate>) -> Unit) {
    
    private val candidatesMap = ConcurrentHashMap<String, MediaCandidate>()
    
    fun addCandidate(candidate: MediaCandidate) {
        val cleanUrl = cleanUrl(candidate.url)
        if (!candidatesMap.containsKey(cleanUrl)) {
            val finalFilename = candidate.filename ?: guessFilename(cleanUrl, candidate.type)
            val newCandidate = candidate.copy(url = cleanUrl, filename = finalFilename)
            candidatesMap[cleanUrl] = newCandidate
            notifyUi()
        }
    }

    fun processJsPayload(jsonString: String) {
        if (jsonString == "null" || jsonString.isBlank()) return
        CoroutineScope(Dispatchers.Default).launch {
            try {
                val array = JSONArray(jsonString)
                for (i in 0 until array.length()) {
                    val obj = array.getJSONObject(i)
                    val url = obj.optString("url")
                    if (url.isBlank() || url.startsWith("data:")) continue
                    
                    val typeStr = obj.optString("type", "UNKNOWN")
                    val type = try { MediaType.valueOf(typeStr) } catch (e: Exception) { MediaType.UNKNOWN }
                    val source = obj.optString("source", "UNKNOWN")
                    val referer = obj.optString("referer")
                    
                    val meta = obj.optJSONObject("meta")
                    val poster = meta?.optString("poster")?.takeIf { it.isNotBlank() }
                    val mimeType = meta?.optString("mimeType")?.takeIf { it.isNotBlank() }
                    
                    addCandidate(MediaCandidate(url, type, source, referer, poster, mimeType))
                }
            } catch (e: Exception) {
                Log.e("MediaDetector", "Error parsing JS payload", e)
            }
        }
    }

    fun clear() {
        candidatesMap.clear()
        notifyUi()
    }
    
    fun getVideos(): List<MediaCandidate> {
        return candidatesMap.values.filter { it.type == MediaType.VIDEO || it.type == MediaType.HLS }.sortedByDescending { it.detectedAt }
    }
    
    fun getPhotos(): List<MediaCandidate> {
        return candidatesMap.values.filter { it.type == MediaType.PHOTO }.sortedByDescending { it.detectedAt }
    }

    private fun notifyUi() {
        val all = candidatesMap.values.toList().sortedByDescending { it.detectedAt }
        CoroutineScope(Dispatchers.Main).launch {
            onCandidatesUpdated(all)
        }
    }

    private fun cleanUrl(url: String): String {
        return url.substringBefore("#")
    }

    private fun guessFilename(url: String, type: MediaType): String {
        return try {
            val path = URL(url).path
            val name = path.substringAfterLast("/")
            if (name.contains(".")) name else {
                "media_${System.currentTimeMillis()}." + when(type) {
                    MediaType.VIDEO -> "mp4"
                    MediaType.PHOTO -> "jpg"
                    MediaType.HLS -> "m3u8"
                    else -> "bin"
                }
            }
        } catch (e: Exception) {
            "media_${System.currentTimeMillis()}"
        }
    }
}

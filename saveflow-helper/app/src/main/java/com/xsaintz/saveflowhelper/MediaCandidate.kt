package com.xsaintz.saveflowhelper

enum class MediaType {
    VIDEO, PHOTO, HLS, UNKNOWN
}

data class MediaCandidate(
    val url: String,
    val type: MediaType,
    val source: String,
    val referer: String,
    val poster: String? = null,
    val mimeType: String? = null,
    val filename: String? = null,
    val detectedAt: Long = System.currentTimeMillis()
)

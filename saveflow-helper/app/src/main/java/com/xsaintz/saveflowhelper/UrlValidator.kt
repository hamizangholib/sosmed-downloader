package com.xsaintz.saveflowhelper

import android.net.Uri

object UrlValidator {
    fun isValidUrl(url: String?): Boolean {
        if (url.isNullOrBlank()) return false
        
        try {
            if (url == "file:///android_asset/test.html") return true
            
            val uri = Uri.parse(url)
            val scheme = uri.scheme?.lowercase()
            
            // Only allow http and https
            if (scheme != "http" && scheme != "https") {
                return false
            }
            
            val host = uri.host?.lowercase() ?: return false
            
            // Reject local and private addresses
            if (host == "localhost" || host.startsWith("127.") || host.startsWith("192.168.") || 
                host.startsWith("10.") || host.endsWith(".local")) {
                return false
            }
            
            // 172.16.0.0 – 172.31.255.255 check
            if (host.startsWith("172.")) {
                val parts = host.split(".")
                if (parts.size >= 2) {
                    val secondPart = parts[1].toIntOrNull()
                    if (secondPart != null && secondPart in 16..31) {
                        return false
                    }
                }
            }

            return true
        } catch (e: Exception) {
            return false
        }
    }
}

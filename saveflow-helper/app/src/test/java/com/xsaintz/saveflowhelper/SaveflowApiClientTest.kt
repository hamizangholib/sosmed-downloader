package com.xsaintz.saveflowhelper

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class SaveflowApiClientTest {
    @Test
    fun `API formats become downloadable video and photo candidates`() {
        val response = JSONObject(
            """
            {
              "items": [{
                "index": 2,
                "title": "Example post",
                "thumbnail": "https://cdn.example/poster.jpg",
                "formats": [
                  {"format_id":"720","label":"720p","ext":"mp4","kind":"video"},
                  {"format_id":"image-1","label":"Original","ext":"jpg","kind":"image"}
                ]
              }]
            }
            """.trimIndent()
        )

        val candidates = SaveflowApiClient("https://api.example").parseCandidates(
            response,
            "https://x.com/user/status/1"
        )

        assertEquals(listOf(MediaType.VIDEO, MediaType.PHOTO), candidates.map { it.type })
        assertTrue(candidates[0].url.startsWith("https://api.example/api/download?"))
        assertTrue(candidates[0].url.contains("index=2"))
        assertTrue(candidates[0].url.contains("format_id=720"))
        assertEquals("Example post_720p.mp4", candidates[0].filename)
    }
}

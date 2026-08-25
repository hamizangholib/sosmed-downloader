package com.xsaintz.saveflowhelper

import android.os.Looper
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class MediaDetectorTest {

    @Test
    fun `test deduplication and basic classification`() {
        var callbacks = 0
        val detector = MediaDetector {
            callbacks++
        }

        detector.addCandidate(MediaCandidate(
            url = "https://example.com/video.mp4#t=10",
            type = MediaType.VIDEO,
            source = "DOM_VIDEO",
            referer = "https://example.com"
        ))

        // Duplicate URL, different fragment, should be deduplicated
        detector.addCandidate(MediaCandidate(
            url = "https://example.com/video.mp4#t=20",
            type = MediaType.VIDEO,
            source = "NETWORK",
            referer = "https://example.com"
        ))

        detector.addCandidate(MediaCandidate(
            url = "https://example.com/image.jpg",
            type = MediaType.PHOTO,
            source = "DOM_IMAGE",
            referer = "https://example.com"
        ))

        val videos = detector.getVideos()
        val photos = detector.getPhotos()

        assertEquals(1, videos.size)
        assertEquals("https://example.com/video.mp4", videos[0].url) // Cleaned URL without fragment
        assertEquals(MediaType.VIDEO, videos[0].type)
        assertEquals("DOM_VIDEO", videos[0].source) // First source was kept

        assertEquals(1, photos.size)
        assertEquals("https://example.com/image.jpg", photos[0].url)

        // Callback was called for each NEW addition (2 additions)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(2, callbacks)
    }
}

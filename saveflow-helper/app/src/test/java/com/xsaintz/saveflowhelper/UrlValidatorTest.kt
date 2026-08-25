package com.xsaintz.saveflowhelper

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class UrlValidatorTest {

    @Test
    fun `test valid public http and https urls`() {
        assertTrue(UrlValidator.isValidUrl("http://example.com"))
        assertTrue(UrlValidator.isValidUrl("https://example.com/path?query=1"))
        assertTrue(UrlValidator.isValidUrl("https://download.xsaintz.my.id/test.mp4"))
    }

    @Test
    fun `test invalid schemes are rejected`() {
        assertFalse(UrlValidator.isValidUrl("javascript:alert(1)"))
        assertFalse(UrlValidator.isValidUrl("file:///etc/passwd"))
        assertFalse(UrlValidator.isValidUrl("content://com.android.providers.media.documents/document/video:123"))
        assertFalse(UrlValidator.isValidUrl("data:image/png;base64,iVBORw0KGgo"))
        assertFalse(UrlValidator.isValidUrl("intent://example.com#Intent;scheme=http;end"))
    }

    @Test
    fun `test localhost and loopback are rejected`() {
        assertFalse(UrlValidator.isValidUrl("http://localhost"))
        assertFalse(UrlValidator.isValidUrl("http://localhost:8080"))
        assertFalse(UrlValidator.isValidUrl("http://127.0.0.1"))
        assertFalse(UrlValidator.isValidUrl("http://127.0.0.1:80"))
    }

    @Test
    fun `test private IPs are rejected`() {
        assertFalse(UrlValidator.isValidUrl("http://192.168.1.1"))
        assertFalse(UrlValidator.isValidUrl("http://10.0.0.5"))
        assertFalse(UrlValidator.isValidUrl("http://172.16.0.1"))
        assertFalse(UrlValidator.isValidUrl("http://172.31.255.255"))
        
        // This is a public IP in the 172 range (172.32.x.x) so it should be allowed
        assertTrue(UrlValidator.isValidUrl("http://172.32.0.1"))
    }
}

package com.xsaintz.saveflowhelper

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import kotlin.math.roundToInt

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class MainActivityLayoutTest {
    @Test
    fun `browser starts hidden and result thumbnails are large`() {
        val activity = Robolectric.buildActivity(MainActivity::class.java).setup().get()
        assertEquals(View.GONE, activity.findViewById<FrameLayout>(R.id.webContainer).visibility)

        val item = LayoutInflater.from(activity).inflate(R.layout.item_media, null)
        val thumbnail = item.findViewById<ImageView>(R.id.ivThumbnail)
        val thumbnailContainer = thumbnail.parent as ViewGroup
        val expectedHeight = (180 * activity.resources.displayMetrics.density).roundToInt()
        assertTrue(thumbnailContainer.layoutParams.height >= expectedHeight)
    }
}

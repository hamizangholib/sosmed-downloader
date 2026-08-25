package com.xsaintz.saveflowhelper

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.LruCache
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.RecyclerView
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class MediaAdapter(
    private var items: List<MediaCandidate>,
    private val downloadManager: MediaDownloadManager
) : RecyclerView.Adapter<MediaAdapter.ViewHolder>() {

    companion object {
        private val thumbnailExecutor = Executors.newFixedThreadPool(3)
        private val mainHandler = Handler(Looper.getMainLooper())
        private val thumbnailCache = object : LruCache<String, Bitmap>(16 * 1024) {
            override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount / 1024
        }
    }

    fun submitList(newItems: List<MediaCandidate>) {
        items = newItems
        notifyDataSetChanged()
    }

    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvFilename: TextView = view.findViewById(R.id.tvFilename)
        val tvType: TextView = view.findViewById(R.id.tvType)
        val tvDomain: TextView = view.findViewById(R.id.tvDomain)
        val btnDownload: Button = view.findViewById(R.id.btnDownload)
        val btnCopy: Button = view.findViewById(R.id.btnCopy)
        val btnOpen: Button = view.findViewById(R.id.btnOpen)
        val ivThumbnail: ImageView = view.findViewById(R.id.ivThumbnail)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.item_media, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        
        holder.tvFilename.text = item.filename ?: "Unknown"
        holder.tvType.text = "${item.type.name} • ${item.source}"
        
        try {
            holder.tvDomain.text = URL(item.url).host
        } catch (e: Exception) {
            holder.tvDomain.text = "Unknown source"
        }

        loadThumbnail(holder, item)
        
        holder.btnDownload.setOnClickListener {
            downloadManager.downloadMedia(item)
        }
        
        holder.btnCopy.setOnClickListener {
            val clipboard = holder.itemView.context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = ClipData.newPlainText("Media URL", item.url)
            clipboard.setPrimaryClip(clip)
            Toast.makeText(holder.itemView.context, "URL copied!", Toast.LENGTH_SHORT).show()
        }
        
        holder.btnOpen.setOnClickListener {
            val intent = Intent(Intent.ACTION_VIEW)
            intent.data = Uri.parse(item.url)
            try {
                holder.itemView.context.startActivity(intent)
            } catch (e: Exception) {
                Toast.makeText(holder.itemView.context, "Cannot open URL", Toast.LENGTH_SHORT).show()
            }
        }
    }

    override fun onViewRecycled(holder: ViewHolder) {
        holder.ivThumbnail.tag = null
        holder.ivThumbnail.setImageDrawable(null)
        super.onViewRecycled(holder)
    }

    private fun loadThumbnail(holder: ViewHolder, item: MediaCandidate) {
        val thumbnailUrl = item.poster ?: item.url.takeIf { item.type == MediaType.PHOTO }
        holder.ivThumbnail.tag = thumbnailUrl
        holder.ivThumbnail.setImageDrawable(null)
        if (thumbnailUrl == null) return

        thumbnailCache.get(thumbnailUrl)?.let {
            holder.ivThumbnail.setImageBitmap(it)
            return
        }
        thumbnailExecutor.execute {
            val bitmap = runCatching { fetchThumbnail(thumbnailUrl, item.referer) }.getOrNull()
            if (bitmap != null) thumbnailCache.put(thumbnailUrl, bitmap)
            mainHandler.post {
                if (holder.ivThumbnail.tag == thumbnailUrl && bitmap != null) {
                    holder.ivThumbnail.setImageBitmap(bitmap)
                }
            }
        }
    }

    private fun fetchThumbnail(url: String, referer: String): Bitmap? {
        val connection = URL(url).openConnection() as HttpURLConnection
        return try {
            connection.connectTimeout = 15_000
            connection.readTimeout = 20_000
            connection.instanceFollowRedirects = true
            connection.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36")
            if (referer.isNotBlank()) connection.setRequestProperty("Referer", referer)
            if (connection.responseCode !in 200..299) return null

            val output = ByteArrayOutputStream()
            connection.inputStream.use { input ->
                val buffer = ByteArray(8 * 1024)
                var total = 0
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    total += count
                    if (total > 5 * 1024 * 1024) return null
                    output.write(buffer, 0, count)
                }
            }
            val bytes = output.toByteArray()
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        } finally {
            connection.disconnect()
        }
    }

    override fun getItemCount() = items.size
}

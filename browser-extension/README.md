# Saveflow Helper

Chromium extension used only when Saveflow's normal server extraction cannot see media that appears after page interaction.

## Load locally

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this `browser-extension` folder.
4. Open Saveflow, submit a link, then choose **Detect from browser tab** when offered.

The extension observes media requests only for a tab opened explicitly from Saveflow. Detected URLs stay in the browser session and are cleared when either associated tab closes.

// Keeps the screen awake during a show. The Wake Lock API needs a secure
// context and CueLight runs over plain HTTP on LAN, so this uses the
// NoSleep.js technique instead: a hidden, muted, looping video. Mobile
// browsers only allow playback after a user gesture, so it starts on the
// first tap anywhere on the page.
(function () {
  var video = null;

  function play() {
    if (video) video.play().catch(function () {});
  }

  function enable() {
    if (video) { play(); return; }
    video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.muted = true;
    video.loop = true;
    video.src = "/static/keepawake.mp4";
    video.style.cssText =
      "position:fixed;left:-10px;top:-10px;width:2px;height:2px;opacity:0;pointer-events:none;";
    document.body.appendChild(video);
    video.play().catch(function () {
      // Playback rejected (gesture not accepted) — retry on the next tap
      video.remove();
      video = null;
    });
  }

  document.addEventListener("touchend", enable, true);
  document.addEventListener("click", enable, true);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) play();
  });
})();

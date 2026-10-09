"use strict";

/*
 * Live Store | Premium Video
 * File: public/app.js
 */

(function () {
  const APP_NAME = "Live Store";
  const STORAGE_KEY = "live_store_videos";

  const videoList = document.getElementById("video-list");

  // Initialize Telegram Web App safely.
  function initializeTelegram() {
    try {
      const tg = window.Telegram && window.Telegram.WebApp;

      if (!tg) return null;

      tg.ready();
      tg.expand();

      try {
        tg.setHeaderColor("#0B0B12");
        tg.setBackgroundColor("#0B0B12");
      } catch (_) {
        // Some Telegram clients may not support these methods.
      }

      return tg;
    } catch (error) {
      console.warn("Telegram initialization unavailable.");
      return null;
    }
  }

  // Accept only HTTP and HTTPS URLs.
  function isValidVideoUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:";
    } catch (_) {
      return false;
    }
  }

  // Read optional video data saved by a compatible admin interface.
  function getVideos() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);

      if (!raw) return [];

      const data = JSON.parse(raw);

      if (!Array.isArray(data)) return [];

      return data.filter(function (video) {
        return (
          video &&
          typeof video.title === "string" &&
          typeof video.url === "string" &&
          isValidVideoUrl(video.url)
        );
      });
    } catch (error) {
      console.warn("Could not read saved videos.");
      return [];
    }
  }

  function createElement(tag, className, textContent) {
    const element = document.createElement(tag);

    if (className) {
      element.className = className;
    }

    if (typeof textContent === "string") {
      element.textContent = textContent;
    }

    return element;
  }

  // Display the empty state when no videos have been added.
  function renderEmptyState() {
    if (!videoList) return;

    videoList.replaceChildren();

    const card = createElement("div", "empty-state");
    const icon = createElement("div", "play-icon", "▶");
    const heading = createElement("h3", "", APP_NAME);
    const description = createElement(
      "p",
      "",
      "No videos available yet. Please check back later."
    );

    card.append(icon, heading, description);
    videoList.appendChild(card);
  }

  // Render each video as a simple card.
  function renderVideos(videos) {
    if (!videoList) return;

    videoList.replaceChildren();

    if (videos.length === 0) {
      renderEmptyState();
      return;
    }

    videos.forEach(function (video) {
      const card = createElement("article", "feature-card");
      const icon = createElement("div", "feature-icon", "▶");
      const content = createElement("div", "feature-copy");
      const title = createElement("h3", "", video.title);
      const description = createElement(
        "p",
        "",
        typeof video.description === "string"
          ? video.description
          : "Watch on Live Store"
      );
      const button = createElement("button", "video-open-button", "Open Video");

      button.type = "button";

      button.addEventListener("click", function () {
        if (!isValidVideoUrl(video.url)) {
          showMessage("This video link is invalid.");
          return;
        }

        // Open the video URL only after the user taps the button.
        const tg = window.Telegram && window.Telegram.WebApp;

        try {
          if (
            tg &&
            typeof tg.openLink === "function" &&
            new URL(video.url).protocol === "https:"
          ) {
            tg.openLink(video.url);
          } else {
            window.open(video.url, "_blank", "noopener,noreferrer");
          }
        } catch (_) {
          showMessage("Unable to open this video link.");
        }
      });

      content.append(title, description, button);
      card.append(icon, content);
      videoList.appendChild(card);
    });
  }

  function showMessage(message) {
    if (window.Telegram && window.Telegram.WebApp) {
      try {
        const tg = window.Telegram.WebApp;

        if (tg.showAlert) {
          tg.showAlert(message);
          return;
        }
      } catch (_) {
        // Fall back to an on-page message.
      }
    }

    window.alert(message);
  }

  function startApp() {
    initializeTelegram();

    const videos = getVideos();
    renderVideos(videos);

    document.title = "Live Store | Premium Video";
  }

  // Start after the HTML document has loaded.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startApp, {
      once: true
    });
  } else {
    startApp();
  }
})();

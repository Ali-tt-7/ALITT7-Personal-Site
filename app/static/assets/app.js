const $ = (selector) => document.querySelector(selector);

const body = document.body;
const themeToggle = $("#themeToggle");
const themeIcon = $("#themeIcon");
const menuButton = $("#menuButton");
const mobileMenu = $("#mobileMenu");
const backTop = $("#backTop");
const cursorGlow = $(".cursor-glow");
const year = $("#year");

year.textContent = new Date().getFullYear();

const savedTheme = localStorage.getItem("alitt7-theme");
if (savedTheme === "light") {
  body.classList.add("light");
  themeIcon.textContent = "☀";
}

themeToggle?.addEventListener("click", () => {
  body.classList.toggle("light");
  const light = body.classList.contains("light");
  localStorage.setItem("alitt7-theme", light ? "light" : "dark");
  themeIcon.textContent = light ? "☀" : "☾";
});

menuButton?.addEventListener("click", () => {
  const open = mobileMenu.classList.toggle("open");
  menuButton.setAttribute("aria-expanded", String(open));
  menuButton.textContent = open ? "×" : "☰";
});

mobileMenu?.querySelectorAll("a").forEach(a => {
  a.addEventListener("click", () => {
    mobileMenu.classList.remove("open");
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.textContent = "☰";
  });
});

document.addEventListener("mousemove", (event) => {
  if (!cursorGlow) return;
  cursorGlow.style.left = `${event.clientX}px`;
  cursorGlow.style.top = `${event.clientY}px`;
});

const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add("visible");
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.12 });

document.querySelectorAll(".reveal").forEach(el => revealObserver.observe(el));

const sections = [...document.querySelectorAll("main section[id]")];
const navLinks = [...document.querySelectorAll(".nav-link")];

const sectionObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    navLinks.forEach(link => link.classList.toggle(
      "active",
      link.getAttribute("href") === `#${entry.target.id}`
    ));
  });
}, { rootMargin: "-35% 0px -55% 0px" });

sections.forEach(section => sectionObserver.observe(section));

window.addEventListener("scroll", () => {
  backTop?.classList.toggle("visible", window.scrollY > 600);
});

backTop?.addEventListener("click", () => window.scrollTo({top: 0, behavior: "smooth"}));

const message = $("#anonymousMessage");
const charCount = $("#charCount");
const form = $("#anonymousForm");
const sendButton = $("#sendButton");
const formStatus = $("#formStatus");
const toast = $("#toast");

message?.addEventListener("input", () => {
  charCount.textContent = `${message.value.length}/1200`;
});

function showToast(text) {
  toast.textContent = text;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3200);
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

  const text = message.value.trim();
  const honeypot = $("#website").value;

  if (!text) return;

  sendButton.disabled = true;
  sendButton.style.opacity = ".65";
  formStatus.textContent = "Sending…";

  try {
    const response = await fetch("/api/anonymous-message", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, website: honeypot})
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Something went wrong.");
    }

    message.value = "";
    charCount.textContent = "0/1200";
    formStatus.textContent = "✓ Your message was sent.";
    showToast("Message sent anonymously.");
  } catch (error) {
    formStatus.textContent = `✕ ${error.message}`;
  } finally {
    sendButton.disabled = false;
    sendButton.style.opacity = "1";
  }
});

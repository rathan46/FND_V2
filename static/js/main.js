document.addEventListener("DOMContentLoaded", () => {
  if (window.AOS) AOS.init({ duration: 750, once: true, offset: 60 });
  if (window.gsap) {
    gsap.from(".glass-nav", { y: -24, opacity: 0, duration: 0.7, ease: "power3.out" });
    gsap.from(".hero-copy > *", { y: 24, opacity: 0, duration: 0.7, stagger: 0.09, ease: "power3.out" });
  }
  setTimeout(() => document.querySelectorAll(".toast-message").forEach((el) => el.remove()), 6000);
  initParticles();
  initDashboardAnalyzer();
  initCharts();
});

function initParticles() {
  const canvas = document.getElementById("particleCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const particles = Array.from({ length: 72 }, () => ({
    x: Math.random(),
    y: Math.random(),
    r: Math.random() * 1.8 + 0.4,
    vx: (Math.random() - 0.5) * 0.0007,
    vy: (Math.random() - 0.5) * 0.0007,
  }));
  const resize = () => {
    canvas.width = window.innerWidth * devicePixelRatio;
    canvas.height = window.innerHeight * devicePixelRatio;
  };
  resize();
  window.addEventListener("resize", resize);
  const draw = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(210, 246, 255, .72)";
    particles.forEach((p) => {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > 1) p.vx *= -1;
      if (p.y < 0 || p.y > 1) p.vy *= -1;
      ctx.beginPath();
      ctx.arc(p.x * canvas.width, p.y * canvas.height, p.r * devicePixelRatio, 0, Math.PI * 2);
      ctx.fill();
    });
    requestAnimationFrame(draw);
  };
  draw();
}

let confidenceChart;
function initDashboardAnalyzer() {
  const form = document.getElementById("analysisForm");
  if (!form) return;
  const loader = document.getElementById("loader");
  const resultCard = document.getElementById("resultCard");
  const download = document.getElementById("downloadReport");
  document.getElementById("clearBtn").addEventListener("click", () => {
    form.reset();
    resultCard.classList.add("d-none");
    download.classList.add("disabled");
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    loader.classList.remove("d-none");
    resultCard.classList.add("d-none");
    const payload = {
      headline: document.getElementById("headline").value,
      content: document.getElementById("content").value,
      source_url: document.getElementById("source_url").value,
    };
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const contentType = response.headers.get("content-type") || "";
      const data = contentType.includes("application/json")
        ? await response.json()
        : { error: await response.text() };
      if (!response.ok) throw new Error(data.error || "Analysis failed");
      renderResult(data);
      download.classList.remove("disabled");
    } catch (error) {
      showToast(error.message, "danger");
    } finally {
      loader.classList.add("d-none");
    }
  });
}

function renderResult(data) {
  const resultCard = document.getElementById("resultCard");
  const badge = document.getElementById("predictionBadge");
  badge.textContent = `${data.prediction} • ${data.risk_level} Risk`;
  badge.className = `verdict ${String(data.prediction).toLowerCase()}`;
  document.getElementById("timestamp").textContent = data.timestamp;
  document.getElementById("confidenceText").textContent = `${data.confidence}% confidence`;
  document.getElementById("summary").textContent = data.verification_summary;
  document.getElementById("reasoning").textContent = data.detailed_reasoning;
  document.getElementById("sourceCred").textContent = data.source_credibility;
  document.getElementById("bias").textContent = data.bias_detection;
  document.getElementById("emotion").textContent = data.emotional_manipulation;
  const refs = document.getElementById("references");
  refs.innerHTML = "";
  (data.similar_references || []).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    refs.appendChild(li);
  });
  const ctx = document.getElementById("confidenceChart");
  if (confidenceChart) confidenceChart.destroy();
  confidenceChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Confidence", "Uncertainty"],
      datasets: [{ data: [data.confidence, 100 - data.confidence], backgroundColor: ["#22d3ee", "rgba(255,255,255,.14)"], borderWidth: 0 }],
    },
    options: { plugins: { legend: { display: false } }, cutout: "72%" },
  });
  resultCard.classList.remove("d-none");
  if (window.gsap) gsap.from(resultCard, { y: 28, opacity: 0, duration: 0.55, ease: "power3.out" });
}

function initCharts() {
  const user = document.getElementById("userStatsChart");
  if (user) {
    new Chart(user, {
      type: "bar",
      data: { labels: ["Real", "Fake"], datasets: [{ data: [user.dataset.real, user.dataset.fake], backgroundColor: ["#34d399", "#fb7185"], borderRadius: 8 }] },
      options: chartOptions(),
    });
  }
  const verdict = document.getElementById("adminVerdictChart");
  if (verdict) {
    new Chart(verdict, {
      type: "doughnut",
      data: { labels: ["Real", "Fake"], datasets: [{ data: [verdict.dataset.real, verdict.dataset.fake], backgroundColor: ["#34d399", "#fb7185"], borderWidth: 0 }] },
      options: chartOptions(),
    });
  }
  const api = document.getElementById("apiUsageChart");
  if (api) {
    new Chart(api, {
      type: "line",
      data: { labels: ["AI", "News"], datasets: [{ data: [api.dataset.ai, api.dataset.news], borderColor: "#22d3ee", backgroundColor: "rgba(34,211,238,.18)", tension: 0.45, fill: true }] },
      options: chartOptions(),
    });
  }
}

function chartOptions() {
  return {
    responsive: true,
    plugins: { legend: { labels: { color: "#dce5f4" } } },
    scales: {
      x: { ticks: { color: "#a7b3c7" }, grid: { color: "rgba(255,255,255,.08)" } },
      y: { ticks: { color: "#a7b3c7" }, grid: { color: "rgba(255,255,255,.08)" } },
    },
  };
}

function showToast(message, category = "info") {
  const stack = document.querySelector(".toast-stack");
  const toast = document.createElement("div");
  toast.className = `toast-message ${category}`;
  toast.textContent = message;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 5200);
}

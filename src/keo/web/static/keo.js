document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-download], button[data-post]");
  if (!button) return;
  button.disabled = true;
  const body = button.dataset.download || null;
  try {
    const response = await fetch(button.dataset.post || "/api/downloads", {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body,
      credentials: "same-origin",
    });
    const data = await response.json();
    button.textContent = response.ok ? label(data) : data.detail || "Failed";
  } catch {
    button.textContent = "Failed";
    button.disabled = false;
  }
});

function label(data) {
  if (data.owned) return "In library";
  if (data.job) return data.job.status === "queued" ? "Queued" : data.job.status;
  if (data.status === "checking") return "Checking…";
  return "Done";
}

const cover = document.getElementById("cover-size");
if (cover) {
  const legal = (value) => {
    const size = Number(value);
    return size >= 96 && size <= 320 && size % 8 === 0 ? size : 160;
  };
  const apply = (size) => {
    document.documentElement.style.setProperty("--cover", `${size}px`);
    cover.value = String(size);
  };
  apply(legal(localStorage.getItem("keo-cover")));
  cover.addEventListener("input", () => {
    const size = legal(cover.value);
    apply(size);
    localStorage.setItem("keo-cover", String(size));
  });
}

const search = document.querySelector("input[list=suggestions]");
if (search) {
  search.addEventListener("input", async () => {
    if (search.value.trim().length < 2) return;
    const response = await fetch(
      `/api/suggestions?q=${encodeURIComponent(search.value)}`,
      { credentials: "same-origin" },
    );
    if (!response.ok) return;
    const options = (await response.json()).map((text) =>
      Object.assign(document.createElement("option"), { value: text }),
    );
    document.getElementById("suggestions").replaceChildren(...options);
  });
}

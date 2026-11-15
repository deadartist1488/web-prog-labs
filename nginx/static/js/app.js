// запрос к API через тот же origin (nginx проксирует на приложение)
fetch("/api/time")
  .then((r) => r.json())
  .then((data) => { document.getElementById("time").textContent = data.time; })
  .catch(() => { document.getElementById("time").textContent = "API недоступно"; });

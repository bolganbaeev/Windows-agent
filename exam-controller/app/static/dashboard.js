(() => {
  const tbody = document.getElementById("agents-body");
  const errorBox = document.getElementById("load-error");
  const count = document.getElementById("agent-count");
  const updatedAt = document.getElementById("updated-at");
  const display = (value) => value || "—";

  function cell(row, value, className) {
    const td = document.createElement("td");
    if (className) td.className = className;
    td.textContent = display(value);
    row.appendChild(td);
    return td;
  }

  function renderAgents(agents) {
    tbody.replaceChildren();
    if (!agents.length) {
      const row = document.createElement("tr");
      row.className = "empty-row";
      const td = document.createElement("td");
      td.colSpan = 8;
      td.textContent = "No agents registered.";
      row.appendChild(td);
      tbody.appendChild(row);
    }
    for (const agent of agents) {
      const row = document.createElement("tr");
      cell(row, agent.hostname, "hostname");
      cell(row, agent.agent_id, "agent-id");
      cell(row, agent.current_ip);
      cell(row, agent.network);
      const statusCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `status status-${String(agent.status || "").toLowerCase()}`;
      badge.textContent = display(agent.status);
      statusCell.appendChild(badge);
      row.appendChild(statusCell);
      cell(row, agent.last_seen);
      cell(row, agent.version);
      const actions = document.createElement("td");
      actions.className = "actions";
      for (const label of ["Change IP", "Open Test"]) {
        const button = document.createElement("button");
        button.type = "button";
        button.disabled = true;
        button.textContent = label;
        actions.appendChild(button);
      }
      row.appendChild(actions);
      tbody.appendChild(row);
    }
    count.textContent = `${agents.length} registered`;
  }

  async function refresh() {
    try {
      const response = await fetch("/api/agents", { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(`Controller returned HTTP ${response.status}`);
      const agents = await response.json();
      renderAgents(agents);
      errorBox.hidden = true;
      errorBox.textContent = "";
      updatedAt.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (_) {
      errorBox.textContent = "Agent data is temporarily unavailable. Retrying automatically.";
      errorBox.hidden = false;
      updatedAt.textContent = "Update failed";
    }
  }

  refresh();
  window.setInterval(refresh, 4000);
})();

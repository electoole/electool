/* Embakasi Ward Electoral Intelligence Dashboard */

const DEMO_BADGE = '<span class="badge bg-secondary ms-1">Demo</span>';
const CAMPAIGN_BADGE = '<span class="badge bg-success ms-1">Campaign</span>';

document.addEventListener('DOMContentLoaded', function() {
    initializeThemeToggle();
    renderAssistantHistory();
    loadStatistics();
    loadPollingStations();
    loadBattlegrounds();
    loadMobilizationPlan();
    loadCandidatePerformance();
    loadCandidatesData();
    loadVoteShiftsData();
    loadSentimentData();
    renderCompetitivenessChart();
    renderMobilizationChart();
    renderResultsSummary();
    wirePlotlyResizeHandlers();
});

function initializeThemeToggle() {
    const root = document.documentElement;
    const button = document.getElementById('theme-toggle');
    const storedTheme = localStorage.getItem('elections-theme');
    const preferredTheme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    const initialTheme = storedTheme || preferredTheme;

    function applyTheme(theme) {
        const normalized = theme === 'dark' ? 'dark' : 'light';
        root.setAttribute('data-theme', normalized);
        localStorage.setItem('elections-theme', normalized);
        if (button) {
            button.setAttribute('aria-label', normalized === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
            button.setAttribute('title', normalized === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
        }
        setTimeout(relayoutVisibleCharts, 50);
    }

    applyTheme(initialTheme);
    if (button) {
        button.addEventListener('click', () => {
            const nextTheme = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            applyTheme(nextTheme);
        });
    }
}

async function fetchAPI(endpoint) {
    try {
        const response = await fetch(`/api${endpoint}`);
        if (!response.ok) throw new Error(`API Error: ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error(`Error fetching ${endpoint}:`, error);
        return null;
    }
}

function esc(value) {
    if (value === null || value === undefined || value === '') return '-';
    return String(value).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function fmtNumber(value) {
    const n = Number(value || 0);
    return n.toLocaleString();
}

function pct(value, decimals = 1) {
    return `${(Number(value || 0) * 100).toFixed(decimals)}%`;
}

function placeholderBadge(row) {
    if (String(row.candidate_name || '').trim() === 'Hon. Silverster Ogina') return CAMPAIGN_BADGE;
    return Number(row.is_placeholder || 0) === 1 || row.source_type === 'demo_placeholder' ? DEMO_BADGE : '';
}

function emptyState(message) {
    return `<div class="alert alert-light border mb-0">${esc(message)}</div>`;
}

function compactPlotLayout(title, height = 360) {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const textColor = isDark ? '#e5edf5' : '#1f2933';
    const gridColor = isDark ? '#304050' : '#d8dee6';
    const narrow = window.innerWidth < 600;
    return {
        title: { text: title, font: { size: 15 } },
        height: narrow ? Math.max(280, height - 40) : height,
        autosize: true,
        margin: narrow ? { l: 54, r: 8, t: 44, b: 42 } : { l: 92, r: 12, t: 48, b: 46 },
        font: { size: narrow ? 10 : 11, color: textColor },
        xaxis: { automargin: true, gridcolor: gridColor, zerolinecolor: gridColor },
        yaxis: { automargin: true, gridcolor: gridColor, zerolinecolor: gridColor },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)'
    };
}

function plotConfig() {
    return {
        responsive: true,
        displayModeBar: false
    };
}

function chartWidth(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return undefined;
    const width = el.clientWidth || el.parentElement?.clientWidth || 640;
    return Math.max(260, width - 4);
}

function relayoutVisibleCharts() {
    document.querySelectorAll('.js-plotly-plot').forEach(el => {
        if (el.offsetParent !== null) {
            Plotly.Plots.resize(el);
        }
    });
}

function wirePlotlyResizeHandlers() {
    window.addEventListener('resize', relayoutVisibleCharts);
    document.querySelectorAll('a[data-bs-toggle="tab"]').forEach(tab => {
        tab.addEventListener('shown.bs.tab', () => {
            setTimeout(relayoutVisibleCharts, 80);
        });
    });
}

async function loadStatistics() {
    const data = await fetchAPI('/statistics');
    if (!data || !data.success) return;
    console.log('Statistics loaded:', data.data);
}

async function loadPollingStations() {
    const data = await fetchAPI('/polling-stations');
    if (!data || !data.success) return;
    const stations = data.data || [];
    const container = document.getElementById('polling-stations-table');
    if (!container) return;
    if (!stations.length) {
        container.innerHTML = emptyState('No polling station records are loaded yet.');
        return;
    }

    let html = `
        <table class="table table-hover table-sm align-middle">
            <thead>
                <tr>
                    <th>Polling Station</th>
                    <th>Centre</th>
                    <th>Registered</th>
                    <th>Margin</th>
                    <th>Turnout</th>
                    <th>Competitiveness</th>
                    <th>Mobilization</th>
                    <th>Score</th>
                </tr>
            </thead>
            <tbody>`;
    stations.forEach(station => {
        html += `
            <tr class="cursor-pointer" onclick="showStationDetail('${esc(station.polling_station_id)}')">
                <td><strong>${esc(station.polling_station_name)}</strong></td>
                <td>${esc(station.sub_location)}</td>
                <td>${fmtNumber(station.registered_voters_2022)}</td>
                <td>${pct(station.win_margin_pct_2022)}</td>
                <td>${pct(station.turnout_rate_2022)}</td>
                <td>${getCompetitivenessBadge(station.competitiveness_2022)}</td>
                <td>${getMobilizationBadge(station.mobilization_tier)}</td>
                <td><span class="badge bg-info">${fmtNumber(station.mobilization_score)}</span></td>
            </tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

async function loadBattlegrounds() {
    const data = await fetchAPI('/battlegrounds');
    if (!data || !data.success) return;
    const battlegrounds = data.data || [];
    const container = document.getElementById('battlegrounds-table');
    if (!container) return;
    if (!battlegrounds.length) {
        container.innerHTML = emptyState('No battleground stations currently match the margin threshold.');
        return;
    }

    let html = `
        <table class="table table-danger table-hover table-sm align-middle">
            <thead class="table-dark">
                <tr>
                    <th>Polling Station</th>
                    <th>1st Votes</th>
                    <th>2nd Votes</th>
                    <th>Margin</th>
                    <th>Registered</th>
                    <th>Score</th>
                </tr>
            </thead>
            <tbody>`;
    battlegrounds.forEach(station => {
        html += `
            <tr>
                <td><strong>${esc(station.polling_station_name)}</strong></td>
                <td>${fmtNumber(station.votes_1st_2022)}</td>
                <td>${fmtNumber(station.votes_2nd_2022)}</td>
                <td><span class="badge bg-danger">${fmtNumber(station.win_margin_2022)}</span></td>
                <td>${fmtNumber(station.registered_voters_2022)}</td>
                <td>${fmtNumber(station.mobilization_score)}</td>
            </tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

async function loadMobilizationPlan() {
    const data = await fetchAPI('/mobilization-plan');
    if (!data || !data.success) return;
    renderMobilizationTable('critical-priority-table', data.critical_priority, 'No stations meet the Critical threshold after current scoring.');
    renderMobilizationTable('high-priority-table', data.high_priority, 'No High Priority stations currently found.');
    renderUntappedTable(data.untapped_voters || []);
}

function renderMobilizationTable(containerId, stations, emptyMessage) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!stations || !stations.length) {
        container.innerHTML = emptyState(emptyMessage);
        return;
    }
    let html = `
        <table class="table table-hover table-sm align-middle">
            <thead class="table-dark">
                <tr><th>Station</th><th>Registered</th><th>Untapped</th><th>Margin</th><th>Score</th></tr>
            </thead><tbody>`;
    stations.forEach(station => {
        html += `
            <tr>
                <td><strong>${esc(station.polling_station_name)}</strong></td>
                <td>${fmtNumber(station.registered_voters_2022)}</td>
                <td>${fmtNumber(station.untapped_voters)}</td>
                <td>${pct(station.win_margin_pct_2022)}</td>
                <td><span class="badge bg-info">${fmtNumber(station.mobilization_score)}</span></td>
            </tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

function renderUntappedTable(stations) {
    const container = document.getElementById('untapped-voters-table');
    if (!container) return;
    if (!stations.length) {
        container.innerHTML = emptyState('No untapped voter records are available.');
        return;
    }
    let html = `
        <table class="table table-hover table-sm align-middle">
            <thead class="table-dark">
                <tr><th>Polling Station</th><th>Registered</th><th>Turnout</th><th>Untapped</th><th>Tier</th></tr>
            </thead><tbody>`;
    stations.forEach(station => {
        html += `
            <tr>
                <td><strong>${esc(station.polling_station_name)}</strong></td>
                <td>${fmtNumber(station.registered_voters_2022)}</td>
                <td>${pct(station.turnout_rate_2022)}</td>
                <td><span class="badge bg-warning text-dark">${fmtNumber(station.untapped_voters)}</span></td>
                <td>${getMobilizationBadge(station.mobilization_tier)}</td>
            </tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

async function loadCandidatePerformance() {
    const data = await fetchAPI('/candidate-performance');
    if (!data || !data.success) return;
    const candidates = data.data || [];
    const container = document.getElementById('results-summary');
    if (!container || !candidates.length) return;
    let html = `
        <table class="table table-sm align-middle">
            <thead class="table-dark">
                <tr><th>Candidate</th><th>Party</th><th>Total Votes</th><th>Source</th></tr>
            </thead><tbody>`;
    candidates.forEach((candidate, index) => {
        html += `
            <tr class="${index === 0 ? 'bg-light' : ''}">
                <td><strong>${esc(candidate.candidate_name)}</strong></td>
                <td>${esc(candidate.party)}</td>
                <td>${fmtNumber(candidate.total_votes)}</td>
                <td>${placeholderBadge(candidate) || '<span class="badge bg-success">Official</span>'}</td>
            </tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

function renderCompetitivenessChart() {
    fetch('/api/polling-stations').then(r => r.json()).then(data => {
        if (!data.success) return;
        const counts = {};
        (data.data || []).forEach(s => counts[s.competitiveness_2022] = (counts[s.competitiveness_2022] || 0) + 1);
        const labels = ['Highly Contested', 'Battleground', 'Leaning', 'Safe'].filter(k => counts[k]);
        const trace = {
            labels,
            values: labels.map(k => counts[k]),
            type: 'pie',
            textinfo: 'label+percent',
            automargin: true,
            marker: { colors: ['#e74c3c', '#f39c12', '#3498db', '#27ae60'] }
        };
        const layout = compactPlotLayout('Polling Stations by Competitiveness', 330);
        layout.width = chartWidth('competitiveness-chart');
        layout.legend = { orientation: 'h', y: -0.08, font: { size: 10 } };
        Plotly.react('competitiveness-chart', [trace], layout, plotConfig());
    });
}

function renderMobilizationChart() {
    fetch('/api/statistics').then(r => r.json()).then(data => {
        if (!data.success) return;
        const tiers = data.data.stations_by_mobilization || {};
        const labels = ['Critical Priority', 'High Priority', 'Medium Priority', 'Low Priority', 'Maintain Support'].filter(k => tiers[k]);
        const trace = {
            x: labels.map(k => tiers[k]),
            y: labels,
            type: 'bar',
            orientation: 'h',
            marker: { color: ['#c0392b', '#e67e22', '#f1c40f', '#3498db', '#27ae60'] }
        };
        const layout = compactPlotLayout('Stations by Mobilization Tier', 330);
        layout.width = chartWidth('mobilization-chart');
        layout.xaxis.title = 'Stations';
        Plotly.react('mobilization-chart', [trace], layout, plotConfig());
    });
}

function renderResultsSummary() {
    fetch('/api/candidate-performance').then(r => r.json()).then(data => {
        if (!data.success) return;
        const candidates = (data.data || []).slice(0, 5).reverse();
        const trace = {
            x: candidates.map(c => c.total_votes),
            y: candidates.map(c => c.candidate_name),
            type: 'bar',
            orientation: 'h',
            marker: { color: '#3498db' }
        };
        const layout = compactPlotLayout('2022 Candidate Vote Totals', 320);
        layout.width = chartWidth('results-summary');
        layout.xaxis.title = 'Votes';
        Plotly.react('results-summary', [trace], layout, plotConfig());
    });
}

async function loadCandidatesData() {
    const data = await fetchAPI('/candidates/performance-history');
    if (!data || !data.success) return;
    const byYear = data.by_year || {};

    renderYearTable('candidates-2017-table', byYear['2017'] || [], '2017');
    renderYearTable('candidates-2022-table', byYear['2022'] || [], '2022');
    renderAspirantsTable('candidates-2027-table', byYear['2027'] || []);
}

function renderYearTable(containerId, rows, year) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!rows.length) {
        container.innerHTML = emptyState(`No ${year} candidate records are loaded.`);
        return;
    }
    let html = `<table class="table table-hover table-sm"><thead class="table-dark"><tr><th>Candidate</th><th>Party</th><th>Winner Votes</th><th>Source</th></tr></thead><tbody>`;
    rows.forEach(c => {
        html += `<tr><td><strong>${esc(c.candidate_name)}</strong></td><td>${esc(c.party)}</td><td>${fmtNumber(c.votes)}</td><td><span class="badge bg-success">Official</span></td></tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

function renderAspirantsTable(containerId, rows) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!rows.length) {
        container.innerHTML = emptyState('No 2027 aspirant records are loaded yet.');
        return;
    }
    let html = `<table class="table table-hover table-sm"><thead class="table-dark"><tr><th>Aspirant</th><th>Party</th><th>Status</th><th>Source</th></tr></thead><tbody>`;
    rows.forEach(c => {
        const principal = c.candidate_name === 'Hon. Silverster Ogina' ? 'table-primary' : '';
        html += `<tr class="${principal}"><td><strong>${esc(c.candidate_name)}</strong></td><td>${esc(c.party)}</td><td>${esc(c.status)}</td><td>${placeholderBadge(c)}</td></tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

async function loadVoteShiftsData() {
    const data = await fetchAPI('/candidates/vote-shift-analysis');
    if (!data || !data.success) return;
    const shifts = data.data || [];
    const container = document.getElementById('historical-results-table');
    if (!container) return;
    let html = `<table class="table table-hover table-sm"><thead class="table-dark"><tr><th>Year</th><th>Winner</th><th>Party</th><th>Winner Votes</th><th>Change vs Previous Winner</th><th>Trend Note</th></tr></thead><tbody>`;
    shifts.forEach(s => {
        const changeClass = s.vote_change > 0 ? 'text-success' : s.vote_change < 0 ? 'text-danger' : '';
        html += `<tr><td>${s.year}</td><td><strong>${esc(s.candidate_name)}</strong></td><td>${esc(s.party)}</td><td>${fmtNumber(s.winner_votes)}</td><td class="${changeClass}">${s.vote_change > 0 ? '+' : ''}${fmtNumber(s.vote_change)}</td><td>${esc(s.trend)}</td></tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
}

async function loadSentimentData() {
    const data = await fetchAPI('/mca/sentiment-summary');
    if (!data || !data.success) return;
    const candidates = data.data || [];
    const container = document.getElementById('sentiment-table');
    if (!container) return;
    if (!candidates.length) {
        container.innerHTML = emptyState('No sentiment records are loaded yet. Upload real sentiment CSV from the admin panel.');
        return;
    }
    let html = `<table class="table table-hover table-sm"><thead class="table-dark"><tr><th>Candidate</th><th>Sentiment</th><th>Level</th><th>Resident Signals</th><th>Source</th></tr></thead><tbody>`;
    candidates.forEach(c => {
        const score = Number(c.sentiment_score || 0);
        const level = score > 0.5 ? 'Very Positive' : score > 0 ? 'Positive' : score < -0.25 ? 'Negative' : 'Neutral';
        const badgeColor = score > 0.5 ? 'bg-success' : score > 0 ? 'bg-info' : score < -0.25 ? 'bg-danger' : 'bg-warning text-dark';
        html += `<tr><td><strong>${esc(c.candidate_name)}</strong></td><td><span class="badge ${badgeColor}">${score.toFixed(2)}</span></td><td>${level}</td><td>${fmtNumber(c.total_mentions)}</td><td>${placeholderBadge(c)}</td></tr>`;
    });
    container.innerHTML = `${html}</tbody></table>`;
    renderVoterIssues();
}

async function renderVoterIssues() {
    const container = document.getElementById('voter-issues');
    if (!container) return;
    const response = await fetchAPI('/mca/social-issues');
    const issues = response?.data || [];
    if (!response?.success || !issues.length) {
        container.innerHTML = emptyState('No social issue records are available yet.');
        return;
    }
    const priorityBadge = priority => ({
        Critical: 'bg-danger',
        High: 'bg-warning text-dark',
        Watch: 'bg-info text-dark'
    }[priority] || 'bg-secondary');
    const topCards = issues.slice(0, 4).map(issue => `
        <div class="col-md-6 col-xl-3">
            <div class="card h-100 issue-response-card">
                <div class="card-body">
                    <div class="d-flex justify-content-between gap-2 align-items-start mb-2">
                        <h6 class="mb-0">${esc(issue.issue)}</h6>
                        <span class="badge ${priorityBadge(issue.priority)}">${esc(issue.priority)}</span>
                    </div>
                    <div class="small text-muted mb-2">${fmtNumber(issue.mentions)} resident signals, sentiment ${Number(issue.sentiment_score || 0).toFixed(2)}</div>
                    <p class="small mb-2"><strong>Signal:</strong> ${esc(issue.voter_signal)}</p>
                    <p class="small mb-0"><strong>Your move:</strong> ${esc(issue.recommended_response)}</p>
                </div>
            </div>
        </div>
    `).join('');
    const rows = issues.map(issue => `
        <tr>
            <td><strong>${esc(issue.issue)}</strong><br><span class="text-muted small">${esc((issue.hotspots || []).join(', '))}</span></td>
            <td><span class="badge ${priorityBadge(issue.priority)}">${esc(issue.priority)}</span></td>
            <td>${fmtNumber(issue.mentions)}</td>
            <td>${Number(issue.sentiment_score || 0).toFixed(2)}</td>
            <td>${esc(issue.field_action)}</td>
            <td>${esc(issue.message)}</td>
        </tr>
    `).join('');
    container.innerHTML = `
        <div class="row g-3 mb-3">${topCards}</div>
        <div class="table-responsive">
            <table class="table table-hover table-sm align-middle">
                <thead class="table-dark">
                    <tr>
                        <th>Issue / Hotspots</th>
                        <th>Priority</th>
                        <th>Resident Signals</th>
                        <th>Sentiment</th>
                        <th>Field Action</th>
                        <th>Campaign Message</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
}

function getCompetitivenessBadge(competitiveness) {
    const badgeClass = {
        'Highly Contested': 'badge-contested',
        'Battleground': 'badge-battleground',
        'Leaning': 'badge-leaning',
        'Safe': 'badge-safe'
    }[competitiveness] || 'bg-secondary';
    return `<span class="badge ${badgeClass}">${esc(competitiveness)}</span>`;
}

function getMobilizationBadge(tier) {
    const badgeClass = {
        'Critical Priority': 'bg-danger',
        'High Priority': 'bg-warning text-dark',
        'Medium Priority': 'bg-info',
        'Low Priority': 'bg-secondary',
        'Maintain Support': 'bg-success'
    }[tier] || 'bg-secondary';
    return `<span class="badge ${badgeClass}">${esc(tier)}</span>`;
}

function showStationDetail(stationId) {
    fetch(`/api/polling-stations/${stationId}`).then(r => r.json()).then(data => {
        if (data.success) {
            const d = data.data;
            alert(`Station: ${d.polling_station_name}\n1st votes: ${fmtNumber(d.votes_1st_2022)}\nTurnout: ${pct(d.turnout_rate_2022)}\nSource: ${d.result_source_type}`);
        }
    });
}

async function askAssistant() {
    const input = document.getElementById('ai-question');
    const answerBox = document.getElementById('ai-answer');
    const btn = document.getElementById('ask-ai-btn');
    const question = (input?.value || '').trim();
    if (!question) {
        answerBox.innerHTML = emptyState('Type a question first.');
        return;
    }
    if (btn) btn.disabled = true;
    answerBox.innerHTML = '<div class="text-muted">Thinking through the dashboard data...</div>';
    try {
        const response = await fetch('/api/assistant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question })
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'Assistant failed');
        const answer = data.answer || '';
        answerBox.innerHTML = `<div class="assistant-response">${formatAssistantAnswer(answer)}</div>`;
        saveAssistantHistory(question, answer);
    } catch (error) {
        answerBox.innerHTML = `<div class="alert alert-danger">Assistant error: ${esc(error.message)}</div>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

function getAssistantHistory() {
    try {
        return JSON.parse(localStorage.getItem('elections-assistant-history') || '[]');
    } catch {
        return [];
    }
}

function setAssistantHistory(items) {
    localStorage.setItem('elections-assistant-history', JSON.stringify(items.slice(0, 12)));
}

function saveAssistantHistory(question, answer) {
    const history = getAssistantHistory();
    history.unshift({
        question,
        answer,
        created_at: new Date().toISOString()
    });
    setAssistantHistory(history);
    renderAssistantHistory();
}

function toggleAssistantHistory() {
    const panel = document.getElementById('ai-history-panel');
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    renderAssistantHistory();
}

function clearAssistantHistory() {
    localStorage.removeItem('elections-assistant-history');
    renderAssistantHistory();
}

function clearAssistantWorkspace() {
    const input = document.getElementById('ai-question');
    const answerBox = document.getElementById('ai-answer');
    if (input) input.value = '';
    if (answerBox) answerBox.innerHTML = '';
}

function openAssistantHistoryItem(index) {
    const item = getAssistantHistory()[index];
    if (!item) return;
    const input = document.getElementById('ai-question');
    const answerBox = document.getElementById('ai-answer');
    if (input) input.value = item.question;
    if (answerBox) answerBox.innerHTML = `<div class="assistant-response">${formatAssistantAnswer(item.answer || '')}</div>`;
}

function renderAssistantHistory() {
    const panel = document.getElementById('ai-history-panel');
    if (!panel) return;
    const history = getAssistantHistory();
    if (!history.length) {
        panel.innerHTML = emptyState('No saved assistant chats yet.');
        return;
    }
    const items = history.map((item, index) => {
        const date = new Date(item.created_at);
        const label = Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
        return `
            <button class="assistant-history-item" type="button" onclick="openAssistantHistoryItem(${index})">
                <strong>${esc(item.question)}</strong>
                <span>${esc(label)}</span>
            </button>`;
    }).join('');
    panel.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <h6 class="mb-0">Saved Chats</h6>
            <button class="btn btn-sm btn-outline-danger" type="button" onclick="clearAssistantHistory()">Clear</button>
        </div>
        <div class="assistant-history-list">${items}</div>`;
}

function formatAssistantAnswer(text) {
    const normalized = String(text || '')
        .replace(/\r\n/g, '\n')
        .replace(/\s+\*\s+(?=\*\*|[A-Za-z0-9])/g, '\n* ')
        .replace(/\s+(\d+\.\s+(?=\*\*|[A-Za-z0-9]))/g, '\n$1')
        .replace(/([^\n])\n([^\n*#\d-])/g, '$1\n\n$2');
    const lines = normalized.split('\n');
    const html = [];
    let paragraph = [];
    let listType = null;

    function inlineMarkdown(value) {
        return esc(value).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    }

    function flushParagraph() {
        if (!paragraph.length) return;
        html.push(`<p>${inlineMarkdown(paragraph.join(' '))}</p>`);
        paragraph = [];
    }

    function closeList() {
        if (!listType) return;
        html.push(`</${listType}>`);
        listType = null;
    }

    lines.forEach(rawLine => {
        const line = rawLine.trim();
        if (!line) {
            flushParagraph();
            closeList();
            return;
        }

        const heading = line.match(/^\*\*(.+?)\*\*:?$/);
        if (heading) {
            flushParagraph();
            closeList();
            html.push(`<h6>${inlineMarkdown(heading[1])}</h6>`);
            return;
        }

        const bullet = line.match(/^[-*]\s+(.+)$/);
        if (bullet) {
            flushParagraph();
            if (listType !== 'ul') {
                closeList();
                html.push('<ul>');
                listType = 'ul';
            }
            html.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
            return;
        }

        const numbered = line.match(/^\d+\.\s+(.+)$/);
        if (numbered) {
            flushParagraph();
            if (listType !== 'ol') {
                closeList();
                html.push('<ol>');
                listType = 'ol';
            }
            html.push(`<li>${inlineMarkdown(numbered[1])}</li>`);
            return;
        }

        closeList();
        paragraph.push(line);
    });

    flushParagraph();
    closeList();
    return html.join('');
}

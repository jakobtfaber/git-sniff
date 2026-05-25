// content.js - Shadow DOM UI Orchestrator

(() => {
  function cleanup() {
    const existingHost = document.getElementById('git-sniff-shadow-host');
    if (existingHost) {
      existingHost.remove();
    }
  }

  async function initializeDashboard() {
    cleanup();

    const pathParts = window.location.pathname.split('/').filter(Boolean);
    if (pathParts.length < 2) return; // Not a repository main or subpage
    
    // Ignore special non-repository GitHub paths at the root (settings, explore, etc.)
    const ignoredRoots = new Set(['settings', 'explore', 'trending', 'marketplace', 'issues', 'pulls', 'notifications']);
    if (ignoredRoots.has(pathParts[0])) return;

    const [owner, repo] = pathParts;

    // Create mount point in light DOM
    const shadowHost = document.createElement('div');
    shadowHost.id = 'git-sniff-shadow-host';
    document.body.appendChild(shadowHost);

    // Attach Closed Shadow DOM for absolute style containment
    const shadowRoot = shadowHost.attachShadow({ mode: 'closed' });

    // Inject Isolated Style Link
    const styleLink = document.createElement('link');
    styleLink.rel = 'stylesheet';
    styleLink.href = chrome.runtime.getURL('content.css');
    shadowRoot.appendChild(styleLink);

    // Formulate HTML UI Skeleton
    const uiWrapper = document.createElement('div');
    uiWrapper.className = 'sniff-ui-wrapper';
    
    uiWrapper.innerHTML = `
      <div class="sniff-pill" id="sniff-pill">
        <span class="status-dot offline"></span>
        <span class="pill-text">Sniffing...</span>
      </div>

      <div class="sniff-panel" id="sniff-panel">
        <div class="panel-header">
          <h2>git-sniff Scorecard</h2>
          <button class="close-btn" id="close-panel">&times;</button>
        </div>
        
        <div class="panel-content">
          <div class="score-circle-container">
            <div class="score-circle" id="score-circle">
              <span class="score-num" id="score-val">--</span>
              <span class="score-label">overall</span>
            </div>
          </div>

          <div class="pillars-section">
            <h3>Analysis Pillars</h3>
            <div class="pillar-row" id="pillar-maintenance">
              <div class="pillar-info"><span>Maintenance</span><span class="val">--</span></div>
              <div class="progress-track"><div class="progress-bar"></div></div>
            </div>
            <div class="pillar-row" id="pillar-cicd">
              <div class="pillar-info"><span>CI/CD</span><span class="val">--</span></div>
              <div class="progress-track"><div class="progress-bar"></div></div>
            </div>
            <div class="pillar-row" id="pillar-dependencies">
              <div class="pillar-info"><span>Dependencies</span><span class="val">--</span></div>
              <div class="progress-track"><div class="progress-bar"></div></div>
            </div>
            <div class="pillar-row" id="pillar-busfactor">
              <div class="pillar-info"><span>Bus Factor</span><span class="val">--</span></div>
              <div class="progress-track"><div class="progress-bar"></div></div>
            </div>
          </div>

          <div class="recommendation-card">
            <h4>Key Recommendation</h4>
            <p id="rec-text">No active scorecard loaded. Click the pill to fetch metric analyses.</p>
          </div>

          <div class="settings-card">
            <div class="status-row">
              <span>Local Service:</span>
              <span class="connection-status offline" id="connection-status">Checking...</span>
            </div>
            <div class="port-row">
              <label for="port-input">Backend Port:</label>
              <div class="input-group">
                <input type="number" id="port-input" value="8000" min="1" max="65535" />
                <button id="save-port">Save</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    shadowRoot.appendChild(uiWrapper);

    const pill = shadowRoot.getElementById('sniff-pill');
    const panel = shadowRoot.getElementById('sniff-panel');
    const closeBtn = shadowRoot.getElementById('close-panel');
    const portInput = shadowRoot.getElementById('port-input');
    const savePortBtn = shadowRoot.getElementById('save-port');

    // Retrieve and set API Port
    const { apiPort = 8000 } = await chrome.storage.local.get('apiPort');
    portInput.value = apiPort;

    pill.addEventListener('click', () => {
      panel.classList.add('open');
      fetchAndRender();
    });

    closeBtn.addEventListener('click', () => {
      panel.classList.remove('open');
    });

    savePortBtn.addEventListener('click', async () => {
      const newPort = parseInt(portInput.value, 10);
      if (newPort > 0 && newPort <= 65535) {
        await chrome.storage.local.set({ apiPort: newPort });
        savePortBtn.textContent = 'Saved!';
        savePortBtn.classList.add('success');
        setTimeout(() => {
          savePortBtn.textContent = 'Save';
          savePortBtn.classList.remove('success');
        }, 1500);
        fetchAndRender();
      } else {
        alert('Please enter a valid port number (1-65535).');
      }
    });

    async function fetchAndRender() {
      pill.querySelector('.pill-text').textContent = 'Loading...';
      pill.querySelector('.status-dot').className = 'status-dot checking';
      
      chrome.runtime.sendMessage({ action: 'fetchScorecard', owner, repo }, (response) => {
        if (chrome.runtime.lastError || !response) {
          renderError(chrome.runtime.lastError?.message || 'Could not communicate with background listener.');
          return;
        }

        if (response.success) {
          renderScorecard(response.data);
        } else {
          renderError(response.error);
        }
      });
    }

    function getScoreColorClass(score) {
      if (score >= 80) return 'health-green';
      if (score >= 50) return 'warning-yellow';
      return 'critical-red';
    }

    function renderScorecard(data) {
      const score = Math.round(data.overall_score || 0);
      
      pill.querySelector('.status-dot').className = `status-dot ${getScoreColorClass(score)}`;
      pill.querySelector('.pill-text').textContent = `Sniff: ${score}`;

      const scoreCircle = shadowRoot.getElementById('score-circle');
      const scoreVal = shadowRoot.getElementById('score-val');
      scoreVal.textContent = score;
      scoreCircle.className = `score-circle ${getScoreColorClass(score)}`;

      const updatePillar = (id, value) => {
        const row = shadowRoot.getElementById(id);
        const valSpan = row.querySelector('.val');
        const bar = row.querySelector('.progress-bar');
        
        const v = Math.round(value || 0);
        valSpan.textContent = `${v}%`;
        bar.style.width = `${v}%`;
        bar.className = `progress-bar ${getScoreColorClass(v)}`;
      };

      updatePillar('pillar-maintenance', data.breakdown?.maintenance ?? data.maintenance ?? 0);
      updatePillar('pillar-cicd', data.breakdown?.cicd ?? data.cicd ?? 0);
      updatePillar('pillar-dependencies', data.breakdown?.dependencies ?? data.dependencies ?? 0);
      updatePillar('pillar-busfactor', data.breakdown?.bus_factor ?? data.bus_factor ?? 0);

      // Secure DOM manipulation prevents HTML Injection XSS
      const recText = shadowRoot.getElementById('rec-text');
      recText.textContent = data.recommendation || '';

      const connStatus = shadowRoot.getElementById('connection-status');
      connStatus.textContent = 'Connected';
      connStatus.className = 'connection-status online';
    }

    function renderError(errorMessage) {
      pill.querySelector('.status-dot').className = 'status-dot offline';
      pill.querySelector('.pill-text').textContent = 'Offline';

      shadowRoot.getElementById('score-val').textContent = '--';
      shadowRoot.getElementById('score-circle').className = 'score-circle offline';
      
      const resetPillar = (id) => {
        const row = shadowRoot.getElementById(id);
        row.querySelector('.val').textContent = '--';
        row.querySelector('.progress-bar').style.width = '0%';
      };
      resetPillar('pillar-maintenance');
      resetPillar('pillar-cicd');
      resetPillar('pillar-dependencies');
      resetPillar('pillar-busfactor');

      // Secure error text escaping, safely appending static code block instructions
      const recText = shadowRoot.getElementById('rec-text');
      recText.textContent = ''; // clear
      
      const errHeader = document.createElement('span');
      errHeader.style.color = '#ef4444';
      errHeader.style.fontWeight = '500';
      errHeader.textContent = `Connection Error: ${errorMessage}`;
      recText.appendChild(errHeader);
      
      const details = document.createElement('span');
      details.style.fontSize = '11px';
      details.style.marginTop = '6px';
      details.style.display = 'block';
      details.style.color = '#a1a1aa';
      details.style.lineHeight = '1.4';
      details.textContent = 'Please make sure your local microservice server is active: ';
      
      const codeBlock = document.createElement('code');
      codeBlock.style.background = 'rgba(0,0,0,0.3)';
      codeBlock.style.padding = '2px 4px';
      codeBlock.style.borderRadius = '4px';
      codeBlock.style.display = 'inline-block';
      codeBlock.style.marginTop = '4px';
      codeBlock.style.fontFamily = 'monospace';
      codeBlock.textContent = `git-sniff --server --port ${portInput.value}`;
      
      details.appendChild(codeBlock);
      recText.appendChild(details);

      const connStatus = shadowRoot.getElementById('connection-status');
      connStatus.textContent = 'Offline';
      connStatus.className = 'connection-status offline';
    }

    // Silent background load check upon navigation
    chrome.runtime.sendMessage({ action: 'fetchScorecard', owner, repo }, (response) => {
      if (response && response.success) {
        renderScorecard(response.data);
      } else {
        renderError(response ? response.error : 'Server not reachable');
      }
    });
  }

  // Hook into GitHub client-side SPA SPA transitions
  document.addEventListener('turbo:load', initializeDashboard);

  // Fallback for initial entry
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    initializeDashboard();
  } else {
    window.addEventListener('DOMContentLoaded', initializeDashboard);
  }
})();

// background.js - Ephemeral Service Worker

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'fetchScorecard') {
    (async () => {
      try {
        const { apiPort = 8000 } = await chrome.storage.local.get('apiPort');
        const url = `http://127.0.0.1:${apiPort}/sniff?repo=${encodeURIComponent(message.owner)}/${encodeURIComponent(message.repo)}`;
        
        console.log(`[git-sniff] Background querying microservice: ${url}`);
        
        // 15-second connection abort controller to accommodate backend stats compilation budgets
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 15000);

        const response = await fetch(url, { 
          method: 'GET',
          signal: controller.signal 
        });
        
        clearTimeout(timeoutId);

        if (!response.ok) {
          let serverMsg = `HTTP Error ${response.status}: ${response.statusText}`;
          try {
            const errData = await response.json();
            if (errData && errData.detail) {
              serverMsg = errData.detail;
            }
          } catch (e) {
            // Non-JSON response, fallback to default
          }
          throw new Error(serverMsg);
        }

        const data = await response.json();
        sendResponse({ success: true, data });
      } catch (error) {
        console.error('[git-sniff] Background Fetch Failed:', error);
        
        let errorMsg = 'Failed to connect to the git-sniff local service.';
        const errName = error && error.name ? error.name : '';
        const errMsg = error && error.message ? error.message : String(error);

        if (errName === 'AbortError') {
          errorMsg = 'Connection timed out. The server is taking too long to compile statistics.';
        } else if (errMsg.includes('Failed to fetch') || errMsg.includes('Failed to connect') || errMsg.includes('NetworkError')) {
          errorMsg = 'Connection refused. Is the git-sniff server running on the configured port?';
        } else {
          errorMsg = errMsg;
        }
        
        sendResponse({ success: false, error: errorMsg });
      }
    })();
    
    return true; // CRITICAL: Keeps message channel open for async sendResponse
  }
});

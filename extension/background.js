// background.js - Ephemeral Service Worker

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'fetchScorecard') {
    (async () => {
      try {
        const { apiPort = 8000 } = await chrome.storage.local.get('apiPort');
        const url = `http://127.0.0.1:${apiPort}/sniff?repo=${encodeURIComponent(message.owner)}/${encodeURIComponent(message.repo)}`;
        
        console.log(`[git-sniff] Background querying microservice: ${url}`);
        
        // 5-second connection abort controller
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const response = await fetch(url, { 
          method: 'GET',
          signal: controller.signal 
        });
        
        clearTimeout(timeoutId);

        if (!response.ok) {
          throw new Error(`HTTP Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        sendResponse({ success: true, data });
      } catch (error) {
        console.error('[git-sniff] Background Fetch Failed:', error);
        
        let errorMsg = 'Failed to connect to the git-sniff local service.';
        if (error.name === 'AbortError') {
          errorMsg = 'Connection timed out. Please check if your backend is running.';
        } else if (error.message.includes('Failed to fetch')) {
          errorMsg = 'Connection refused. Is the git-sniff server running on the configured port?';
        } else {
          errorMsg = error.message;
        }
        
        sendResponse({ success: false, error: errorMsg });
      }
    })();
    
    return true; // CRITICAL: Keeps message channel open for async sendResponse
  }
});

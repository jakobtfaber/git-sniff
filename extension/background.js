// background.js - Ephemeral Service Worker (Native Messaging transport)

const HOST_NAME = "com.jakobtfaber.git_sniff";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'fetchScorecard') {
    console.log(`[git-sniff] Querying native host for ${message.owner}/${message.repo}`);

    chrome.runtime.sendNativeMessage(
      HOST_NAME,
      { owner: message.owner, repo: message.repo },
      (response) => {
        if (chrome.runtime.lastError) {
          console.error('[git-sniff] Native host error:', chrome.runtime.lastError.message);
          sendResponse({
            success: false,
            error: 'Native host not installed. Run: git-sniff-host --install'
          });
          return;
        }
        if (!response) {
          sendResponse({ success: false, error: 'No response from the git-sniff native host.' });
          return;
        }
        if (response.error) {
          sendResponse({ success: false, error: response.error });
          return;
        }
        sendResponse({ success: true, data: response });
      }
    );

    return true; // keep the message channel open for the async sendResponse
  }
});

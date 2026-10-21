# Facebook comments setup

Connect a Facebook Page so Synthra can classify comments on your posts, reply publicly or privately, hide or delete spam and abuse, and let your team moderate from the dashboard.

> Meta renames menus and moves screens often. The names below follow Meta's documentation; if something has moved, search the App Dashboard for the same name.

## One webhook for Facebook inbox and comments

Meta lets an app register **one callback URL for the Page object**, and both Messenger messages and Page feed events (comments) arrive on it. Synthra therefore uses **one Facebook webhook** for both channels:

- Callback URL: `https://api.example.com/webhooks/<workspace-id>/facebook`. It is the same URL and the same verify token on the Facebook inbox and Facebook comments pages.
- Subscribe the Page object to `messages`, `messaging_postbacks` and `feed` once. Synthra sends each event to the right channel.
- Use one Meta app for both. Each channel keeps its own credentials, so enter the same Page ID, Page access token and app secret on both pages (a channel only receives events that its own app secret verifies).
- Only need one of the two? Set up just that channel. Events for a channel you have not set up, or have turned off, are ignored.

Webhook URLs from older versions (`.../facebook_inbox`, `.../facebook_comments`) keep working but serve only that one channel.

## What you need

- A Facebook **Page** you administer and a Meta developer account.
- Synthra on a public HTTPS URL ([self-hosting](../self-hosting.md)) and a connected workspace database.

## 1. Create the Meta app

App Dashboard > **Create app** > Business type, with a use case that includes managing your Page (Pages API). Add the products Meta suggests for Page management and webhooks.

## 2. Collect the values

| Dashboard field (Synthra) | Where to find it |
| --- | --- |
| Page ID | Your Page > **About > Page transparency > Page ID** (numeric). |
| App secret | **App settings > Basic > App secret** (click Show). |
| Page access token | Step 3. |

## 3. Page access token

Generate a Page access token for your Page with these permissions:

- `pages_read_engagement` (read comments)
- `pages_manage_engagement` (reply, hide, delete comments)
- `pages_manage_posts` (required by Meta for some comment operations)

A convenient way is the **Graph API Explorer** (developers.facebook.com/tools/explorer): choose your app, request those permissions as a **User token**, then switch to your **Page** and copy its token. Page tokens derived from a long-lived user token do not expire; confirm this in the **Access Token Debugger** (expiry "Never" or far away). Regenerate if the expiry is short.

Treat the token like a password.

## 4. Fill in the Synthra channel page

Dashboard > **Settings > Channels > Facebook comments**:

1. Paste Page ID, Page access token and app secret; add your LLM provider and key for automatic replies. Save.
2. Choose bot behaviour: system prompt and tone, and what to do with **spam** and **abusive** comments (`none`, `hide` or `delete`).
3. Copy the **Webhook URL** and **Verify token**. The URL looks like `https://api.example.com/webhooks/<workspace-id>/facebook` and is the same on the Facebook inbox page.
4. Click **Test connection**.

## 5. Register the webhook in Meta

1. App Dashboard > **Webhooks** > object **Page** > **Subscribe to this object**.
2. **Callback URL** and **Verify token**: the values from Synthra. **Verify and save**. This is the same webhook as [Facebook inbox](facebook-inbox.md); if it is already registered, skip to the next step.
3. Subscribe to the **`feed`** field (and **`messages`** and **`messaging_postbacks`** if you also use the inbox).
4. Make sure your Page is subscribed to the app so it actually delivers events.

## 6. Test

In development mode only people with a role on the app trigger webhooks. Post on your Page, comment from a tester account, and the comment should appear in **Comments > Facebook** within seconds. The bot replies if it is on.

## How moderation behaves

- **Reply publicly** posts a comment reply. **Reply privately** sends a Messenger message to the commenter. Meta allows **one private reply per comment**, within **7 days** of the comment.
- **Hide** keeps the comment but removes it from public view.
- **Delete** calls Facebook first and removes the record from your database **only if Facebook confirms**. If Facebook refuses, the comment stays in your list with the error. Bulk delete reports each failure separately.
- Facebook also sends a `remove` event when a comment is deleted on its side; Synthra receives and queues it.

## Going live

Switch the app to **Live**, complete **App Review** for the permissions above (Advanced Access), and complete Business verification where Meta requires it. Provide a privacy policy URL and screencasts of comment moderation.

## Common errors

| Symptom | Cause and fix |
| --- | --- |
| "Callback URL or verify token couldn't be validated" | Verify token mismatch or the URL is not reachable over public HTTPS. |
| Verified, but no comments arrive | The `feed` field is not subscribed, the Page is not subscribed to the app, the comment was written by someone with no role on a development-mode app, or the Page ID in Synthra is wrong. |
| Meta shows 403 for deliveries | Wrong **app secret** saved in Synthra. |
| Messages or comments arrive and are ignored | The channel for that kind of event is not set up, is turned off, or was saved with a different app secret. Set up both Facebook channels with the same Page ID, token and app secret. |
| Error 190 | Token invalid or expired. Regenerate and save. |
| Error 10 or 200 | Missing permission, or Advanced Access not granted yet. |
| Private reply fails | A private reply was already sent for that comment, or the comment is older than 7 days. |
| Delete fails but the comment is already gone on Facebook | It was removed another way (for example by a Page admin). Check how the dashboard handles that case before bulk-deleting; the platform-first rule means a failed call keeps the record. |
| Bot hides or deletes too much | Set `spam_action` and `abuse_action` to `none` and review the system prompt; tune before enabling deletion. |

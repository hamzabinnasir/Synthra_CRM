# Facebook inbox (Messenger) setup

Connect a Facebook Page so the bot answers Messenger conversations and your team can reply or take over from the dashboard.

> Meta renames menus and moves screens often. The names below follow Meta's documentation; if something has moved, search the App Dashboard for the same name.

## One webhook for Facebook inbox and comments

Meta lets an app register **one callback URL for the Page object**, and both Messenger messages and Page feed events (comments) arrive on it. Synthra therefore uses **one Facebook webhook** for both channels:

- Callback URL: `https://api.example.com/webhooks/<workspace-id>/facebook`. It is the same URL and the same verify token on the Facebook inbox and Facebook comments pages.
- Subscribe the Page object to `messages`, `messaging_postbacks` and `feed` once. Synthra sends each event to the right channel.
- Use one Meta app for both. Each channel keeps its own credentials, so enter the same Page ID, Page access token and app secret on both pages (a channel only receives events that its own app secret verifies).
- Only need one of the two? Set up just that channel. Events for a channel you have not set up, or have turned off, are ignored.

Webhook URLs from older versions (`.../facebook_inbox`, `.../facebook_comments`) keep working but serve only that one channel.

## What you need

- A Facebook **Page** you administer (not a personal profile) and a Meta developer account.
- Synthra reachable on a public HTTPS URL (see [self-hosting](../self-hosting.md)).
- A connected workspace database (Dashboard > Settings > Database).

## 1. Create the Meta app

1. App Dashboard > **Create app** > Business type, with the Messenger use case (engage with customers on Messenger).
2. Make sure the **Messenger** product is added to the app.

## 2. Collect the values

| Dashboard field (Synthra) | Where to find it |
| --- | --- |
| Page ID | Your Facebook Page > **About > Page transparency > Page ID** (numeric). |
| App secret | **App settings > Basic > App secret** (click Show). Used to verify that webhooks really come from Meta. |
| Page access token | Step 3. |

## 3. Generate the Page access token

In the App Dashboard > **Messenger > API settings** (Messenger API settings), under **Access tokens**:

1. **Add or remove Pages** and grant the app access to your Page. Accept the permissions Meta lists, including `pages_messaging` and `pages_manage_metadata`.
2. Click **Generate token** next to the Page and copy it.

Check it in Meta's **Access Token Debugger** (developers.facebook.com/tools/debug/accesstoken): a good token shows your app, the Page, the permissions above, and an expiry of "Never" or far in the future. Short expiry means it was generated from a short-lived user token; regenerate it.

Treat the token like a password. Anyone holding it can act as your Page.

## 4. Fill in the Synthra channel page

Dashboard > **Settings > Channels > Facebook inbox**:

1. Paste Page ID, Page access token and app secret. Add your LLM provider and key if the bot should answer automatically. Save (secrets are stored encrypted and shown masked).
2. Copy the **Webhook URL** and **Verify token** from the page. The URL looks like `https://api.example.com/webhooks/<workspace-id>/facebook` and is the same on the Facebook comments page.
3. Click **Test connection**.

## 5. Register the webhook in Meta

1. App Dashboard > **Messenger > API settings > Webhooks > Configure** (or Webhooks product > Page).
2. **Callback URL** and **Verify token**: the values from Synthra. Click **Verify and save**.
3. Subscribe the **Page** to the app (Add subscriptions on the Page) and tick **`messages`** and **`messaging_postbacks`**, plus **`feed`** if you also use [Facebook comments](facebook-comments.md). This is the same single webhook for both channels, so if it is already registered you only need to add the missing fields.

A Page that is not subscribed to the app delivers nothing, even when the callback verified.

## 6. Test

While the app is in **development mode**, only people with a role on the app (admin, developer, tester) can message the Page and trigger the bot. Add a tester, message your Page from their Facebook account, and the conversation should appear in **Inbox > Facebook** within seconds.

## Going live

To answer the general public, switch the app to **Live** and complete **App Review** for **Advanced Access** to `pages_messaging` (and complete **Business verification** where Meta asks for it). You need a public privacy policy URL and a screencast showing the feature. Review takes days; plan for it.

## The 24-hour rule

Messenger allows standard replies within **24 hours of the person's last message**. Outside that window, sends are rejected unless a specific Meta-approved message tag applies. The bot normally answers instantly, so this mostly affects human replies to old conversations.

## Common errors

| Symptom | Cause and fix |
| --- | --- |
| "Callback URL or verify token couldn't be validated" | Verify token mismatch, URL not public HTTPS with a valid certificate, or the API is unreachable. Test `https://api.example.com/readyz` from outside. |
| Verified, but no messages arrive | The Page is not subscribed to the app, `messages` is not ticked, the app is in development mode and the sender has no role on it, or the Page ID in Synthra is wrong (events for other Pages are ignored). |
| Nothing arrives and Meta shows deliveries failing with 403 | Wrong **app secret** saved in Synthra. Every signature mismatch returns 403. |
| Comments arrive in the wrong place or never | The `feed` field is not subscribed on the webhook, or the Facebook comments channel is not set up, is turned off, or was saved with a different app secret. Set up both channels with the same Page ID, token and app secret (see top). |
| Error code 190 (invalid or expired token) | Regenerate the Page token and save it again. Check expiry in the debugger. |
| Error code 10 or 200 (permission) | The token lacks `pages_messaging` or the app does not have Advanced Access yet. |
| Error code 551 or "person isn't available" | The user blocked the Page or cannot receive messages from it. |
| Bot does not answer | Bot switched off for the channel or customer, a human handoff is active, the daily LLM call cap was reached, or the LLM key is missing or invalid. |

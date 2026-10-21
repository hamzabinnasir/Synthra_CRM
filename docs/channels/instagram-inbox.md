# Instagram inbox setup

Connect an Instagram professional account so the bot answers direct messages and your team can reply or take over from the dashboard.

Synthra uses the **Instagram API with Instagram Login**. It does **not** need a Facebook Page linked to the Instagram account.

> Meta renames menus and moves screens often. The names below follow Meta's documentation; if something has moved, search the App Dashboard for the same name.

## One webhook for Instagram inbox and comments

Meta lets an app register **one callback URL for the Instagram object**, and both direct messages and comments arrive on it. Synthra therefore uses **one Instagram webhook** for both channels:

- Callback URL: `https://api.example.com/webhooks/<workspace-id>/instagram`. It is the same URL and the same verify token on the Instagram inbox and Instagram comments pages.
- Subscribe once to the `messages` and `comments` fields. Synthra sends each event to the right channel.
- Use one Meta app for both. Each channel keeps its own credentials, so enter the same account ID, access token and **Instagram** app secret on both pages (a channel only receives events that its own app secret verifies).
- Only need one of the two? Set up just that channel. Events for a channel you have not set up, or have turned off, are ignored.

Webhook URLs from older versions (`.../instagram_inbox`, `.../instagram_comments`) keep working but serve only that one channel.

## What you need

- An Instagram **Business or Creator** account. Personal accounts cannot use the API. Convert in the Instagram app: Settings > Account type and tools > Switch to professional account.
- A Meta developer account, and Synthra on a public HTTPS URL ([self-hosting](../self-hosting.md)) with a connected workspace database.

## 1. Create the Meta app

1. App Dashboard > **Create app** > Business type, and add the **Instagram** product. **API setup with Instagram login** is added automatically.
2. While the app is in development mode, add the Instagram account as a tester: **App roles > Roles > Add people > Instagram Tester**, then accept the invitation in the Instagram app or at instagram.com under Apps and websites > Tester invites.

## 2. Collect the values

All in **App Dashboard > Instagram > API setup with Instagram login**:

| Dashboard field (Synthra) | Where to find it |
| --- | --- |
| Instagram account ID | Step 1 (generate access tokens), next to the connected account. Numeric. |
| Instagram access token | Step 1: **Add account**, log in as the Instagram account, then **Generate token**. Needs `instagram_business_basic` and `instagram_business_manage_messages`. |
| App secret | **Step 3 (Set up Instagram business login) > Business login settings > Instagram app secret.** This is **not** the Facebook app secret from App settings > Basic. Using the wrong one makes every webhook fail the signature check. |

Treat the token as a password. Instagram user tokens are **long-lived but not permanent (about 60 days)**; plan to replace it before it expires, or messages will start failing with an authentication error.

## 3. Fill in the Synthra channel page

Dashboard > **Settings > Channels > Instagram inbox**:

1. Paste the account ID, access token and **Instagram** app secret. Add your LLM provider and key for automatic replies. Save (secrets are stored encrypted and shown masked).
2. Copy the **Webhook URL** and **Verify token**. The URL looks like `https://api.example.com/webhooks/<workspace-id>/instagram` and is the same on the Instagram comments page.
3. Click **Test connection**.

## 4. Register the webhook in Meta

In **Instagram > API setup with Instagram login > Configure webhooks**:

1. **Callback URL** and **Verify token**: the values from Synthra. **Verify and save**.
2. Subscribe to the **`messages`** field (and **`comments`** if you also use [Instagram comments](instagram-comments.md)). This is the same single webhook for both channels.
3. Make sure webhook delivery is enabled for the connected Instagram account (the account toggle in step 1 of the same page).

## 5. Allow message access on the Instagram account

In the Instagram app: **Settings > Messages and story replies > Message controls** (wording varies by version) and enable **Allow access to messages** for connected tools. Without it the API may not receive or send messages.

## 6. Test

From another Instagram account (one with a role on the app while in development mode) send a direct message to your professional account. The conversation should appear in **Inbox > Instagram** within seconds.

## Going live

Switch the app to **Live** and complete **App Review**. You need **Advanced Access** to `instagram_business_manage_messages` if the app serves Instagram accounts you do not own; accounts you own and added to the app work with Standard Access. Provide a public privacy policy URL and a screencast.

## The 24-hour rule

Instagram lets you reply to a person within **24 hours of their last message**. The bot answers immediately; later human replies to old conversations may be rejected by Meta.

## Common errors

| Symptom | Cause and fix |
| --- | --- |
| "Callback URL or verify token couldn't be validated" | Verify token mismatch, URL not public HTTPS with a valid certificate, or the API unreachable. |
| Verified, but no messages arrive | `messages` not subscribed, the account's webhook toggle is off, "Allow access to messages" is off, the sender has no role on a development-mode app, or the account ID in Synthra is wrong (events for other accounts are ignored). |
| Meta shows 403 for deliveries | You saved the Facebook app secret instead of the **Instagram app secret** (or the other way round on another channel). |
| Comments or messages arrive and are ignored | The channel for that kind of event is not set up, is turned off, or was saved with a different Instagram app secret. Set up both Instagram channels with the same account ID, token and app secret. |
| Error 190 / invalid OAuth token | The token expired or was revoked (for example after a password change). Generate a new one and save it. |
| Permission error | The token lacks `instagram_business_manage_messages`, or Advanced Access is missing for an account you do not own. |
| "Instagram account is not a professional account" | Convert it to Business or Creator. |
| Bot does not answer | Bot switched off for the channel or customer, a human handoff is active, the daily LLM call cap was reached, or the LLM key is missing or invalid. |

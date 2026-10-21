# Instagram comments setup

Connect an Instagram professional account so Synthra can classify comments on your posts and reels, reply, hide or delete spam and abuse, and let your team moderate from the dashboard.

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

- An Instagram **Business or Creator** account (Settings > Account type and tools > Switch to professional account).
- A Meta developer account, Synthra on a public HTTPS URL ([self-hosting](../self-hosting.md)) and a connected workspace database.

## 1. Create the Meta app

1. App Dashboard > **Create app** > Business type, and add the **Instagram** product (**API setup with Instagram login** is added automatically).
2. In development mode, add the Instagram account as a tester: **App roles > Roles > Add people > Instagram Tester**, then accept the invitation in Instagram (Apps and websites > Tester invites).

## 2. Collect the values

All in **App Dashboard > Instagram > API setup with Instagram login**:

| Dashboard field (Synthra) | Where to find it |
| --- | --- |
| Instagram account ID | Step 1, next to the connected account. Numeric. |
| Instagram access token | Step 1: **Add account**, log in as the Instagram account, **Generate token**. Needs `instagram_business_basic` and `instagram_business_manage_comments`. |
| App secret | **Step 3 > Business login settings > Instagram app secret.** Not the Facebook app secret from App settings > Basic. |

Instagram user tokens are **long-lived but not permanent (about 60 days)**. Plan to replace the token before it expires, or moderation calls will start failing with an authentication error.

## 3. Fill in the Synthra channel page

Dashboard > **Settings > Channels > Instagram comments**:

1. Paste the account ID, access token and **Instagram** app secret; add your LLM provider and key for automatic replies. Save.
2. Choose bot behaviour: system prompt, tone, and what to do with **spam** and **abusive** comments (`none`, `hide` or `delete`).
3. Copy the **Webhook URL** and **Verify token**. The URL looks like `https://api.example.com/webhooks/<workspace-id>/instagram` and is the same on the Instagram inbox page.
4. Click **Test connection**.

## 4. Register the webhook in Meta

In **Instagram > API setup with Instagram login > Configure webhooks**:

1. **Callback URL** and **Verify token**: the values from Synthra. **Verify and save**.
2. Subscribe to the **`comments`** field (and **`messages`** if you also use [Instagram inbox](instagram-inbox.md)). This is the same single webhook for both channels.
3. Make sure webhook delivery is enabled for the connected Instagram account.

## 5. Test

Comment on one of your posts from a tester account (an account with a role on the app while in development mode). The comment should appear in **Comments > Instagram** within seconds.

Important limits from Meta: **webhooks are not sent for comments on media created by an account that is set to private**, so a private account will never deliver comments.

## How moderation behaves

- **Reply** posts a public reply to the comment. **Private reply** sends a direct message in response to a comment. Meta allows one private reply per comment, within 7 days.
- **Hide** hides the comment from public view without deleting it.
- **Delete** calls Instagram first and removes the record from your database **only if Instagram confirms**. If Instagram refuses, the comment stays in your list with the error. Bulk delete reports each failure separately.
- The API manages comments on media owned by the connected professional account.

## Going live

Switch the app to **Live** and complete **App Review** for `instagram_business_manage_comments`. **Advanced Access** is needed when the app serves Instagram accounts you do not own; accounts you own and added to the app work with Standard Access. Provide a public privacy policy URL and a screencast.

## Common errors

| Symptom | Cause and fix |
| --- | --- |
| "Callback URL or verify token couldn't be validated" | Verify token mismatch, URL not public HTTPS with a valid certificate, or the API unreachable. |
| Verified, but no comments arrive | `comments` not subscribed, the account's webhook toggle is off, the account is private, the commenter has no role on a development-mode app, or the account ID in Synthra is wrong. |
| Meta shows 403 for deliveries | The Facebook app secret was saved instead of the **Instagram app secret**. |
| Comments or messages arrive and are ignored | The channel for that kind of event is not set up, is turned off, or was saved with a different Instagram app secret. Set up both Instagram channels with the same account ID, token and app secret. |
| Error 190 / invalid OAuth token | The token expired or was revoked. Generate a new one and save it. |
| Permission error | The token lacks `instagram_business_manage_comments`, or Advanced Access is missing for an account you do not own. |
| Private reply fails | One was already sent for that comment, or it is older than 7 days. |
| Bot hides or deletes too much | Set `spam_action` and `abuse_action` to `none`, review the system prompt, tune, then enable. |

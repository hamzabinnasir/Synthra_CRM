# WhatsApp inbox setup

Connect a WhatsApp Business number so the bot answers customer messages and your team can take over from the dashboard.

> Meta renames menus and moves screens often. The names below follow Meta's documentation; if something has moved, search the App Dashboard for the same name.

## What you need

- A Meta developer account (https://developers.facebook.com) and a Business portfolio in Meta Business Suite.
- A phone number for WhatsApp Business. A number already registered in the regular WhatsApp or WhatsApp Business app must be removed from it before the Cloud API can use it. For testing, Meta gives you a free test number.
- Synthra CRM reachable on a public HTTPS URL (see [self-hosting](../self-hosting.md)). Meta cannot call `localhost`.
- A connected workspace database (Dashboard > Settings > Database) before the bot can store conversations.

## 1. Create the Meta app

1. App Dashboard > **Create app**. Choose the **Business** type (use case: connect with customers through WhatsApp).
2. Add the **WhatsApp** product to the app.
3. Link or create a WhatsApp Business account when asked.

## 2. Collect the values

In **App Dashboard > WhatsApp > API setup**:

| Dashboard field (Synthra) | Where to find it |
| --- | --- |
| Phone number ID | API setup, under the sender phone number. This is a long numeric ID, **not** the phone number. |
| WhatsApp Business account ID (optional) | API setup, next to the phone number ID. Only needed to list message templates. |
| App secret | **App settings > Basic > App secret** (click Show). Synthra uses it to prove each webhook really comes from Meta. |
| Access token | See step 3. |

## 3. Create a permanent access token

The "Generate access token" button on API setup gives a **temporary token that expires after 24 hours**. It is fine for a first test, never for production.

For a token that does not expire:

1. Business Settings > **Users > System users** > **Add**. Create a system user with the Admin role.
2. Select it > **Assign assets**: add your app and your WhatsApp account with full control.
3. **Generate token**: choose your app, tick `whatsapp_business_messaging` and `whatsapp_business_management`, set expiration to **Never**, and copy the token immediately (Meta shows it once).

Treat this token like a password. Anyone holding it can send messages as your business.

## 4. Fill in the Synthra channel page

Dashboard > **Settings > Channels > WhatsApp inbox**:

1. Paste the phone number ID, access token and app secret. Add your LLM provider and key if the bot should answer automatically.
2. Save. The secrets are encrypted and shown masked afterwards; they are never sent back to your browser.
3. Copy the **Webhook URL** and **Verify token** shown on the page. The URL looks like `https://api.example.com/webhooks/<workspace-id>/whatsapp`.
4. Click **Test connection**. It should report connected.

## 5. Register the webhook in Meta

1. App Dashboard > **WhatsApp > Configuration** > Webhook > **Edit**.
2. **Callback URL**: the Webhook URL from Synthra. **Verify token**: the token from Synthra. Click **Verify and save**.
3. Under webhook fields click **Manage** and subscribe to **`messages`**.

If verification passes, the handshake worked. From now on every incoming message reaches Synthra.

## 6. Test

Send a WhatsApp message from your phone to the business number (while the app is in development mode, add your phone as a recipient under API setup first). The conversation should appear in **Inbox > WhatsApp** within a few seconds, and the bot should reply if it is switched on.

## Going live

- A development-mode app can only talk to numbers you add as test recipients. To serve the public you need the business verified, a **real phone number** and your display name approved, and App Review for the permissions if the app serves businesses you do not own.
- Keep a published **privacy policy** URL ready; Meta requires one.

## The 24-hour rule

WhatsApp lets you send free-form replies only within **24 hours of the customer's last message**. After that only pre-approved template messages are accepted. The bot answers inside the window; messages after it fail with a Meta error. Consider a handoff message that asks the customer to write again.

## Common errors

| Symptom | Cause and fix |
| --- | --- |
| "Callback URL or verify token couldn't be validated" | The verify token differs from the one in Synthra, the URL is not public HTTPS with a valid certificate, or the API is down. Open `https://api.example.com/readyz` from outside your network. |
| Meta verifies but no messages arrive | The `messages` field is not subscribed, or you messaged from a number that is not a test recipient while in development mode. |
| Messages arrive in Meta logs but Synthra shows nothing | Wrong **app secret** saved. Synthra answers 403 to every request whose signature does not match. Re-copy it from App settings > Basic. |
| Webhook works for a day, then sending fails with error code 190 | You used the temporary 24-hour token. Replace it with a system user token. |
| Error 131047 (re-engagement) when the bot replies | The customer's last message is older than 24 hours. Use a template or wait for them to write. |
| Permission error when sending | The system user is missing the asset assignment or one of the two permissions. |
| Test connection fails but the token looks right | The phone number ID is the phone number itself instead of the numeric ID, or the token belongs to a different Business account. |
| Bot does not answer | Bot switched off for the channel or for that customer, a human handoff is active, the daily LLM call cap was reached, or the LLM key is missing or invalid. |

Several phone numbers on one Meta app share one webhook. Synthra ignores events for numbers other than the one configured in the channel.

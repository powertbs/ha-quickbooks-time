<img src="brand/icon.png" alt="QuickBooks Time" width="96" align="right">

# QuickBooks Time (TSheets) for Home Assistant

Turn any Home Assistant dashboard into an office time clock for **QuickBooks Time** (formerly TSheets):

- **Time Clock card:** employees tap the card, type their 4-digit PIN, see their photo and status, and tap **Clock In** or **Clock Out**.
- **Who's In card:** photo, name, job, and first clock-in time today for everyone on the clock.
- **Entities for automations:** who's on the clock, their current job, GPS location from the QuickBooks Time app, and events on every punch.

Both cards come with the integration. You don't need to install any other cards or add Resources.

---

## 1. Get your QuickBooks Time API token

You need to be a QuickBooks Time **admin** for this.

1. Sign in at [tsheets.intuit.com](https://tsheets.intuit.com) (QuickBooks Time).
2. Open **Feature Add-ons → Manage Add-ons**.
3. Find **API** and click **Install**. If it's already installed, click **Preferences**.
4. Click **Add a new application**.
   - **Name:** `Home Assistant` (any name works).
   - **Description / Redirect URL:** leave empty, or type anything. They aren't used.
5. Click **Save**. The new application shows an **Access Token** (a long code starting with `S.`). Copy it.

> **Keep the token private.** Anyone with it can read and edit your company's timesheets. If it ever leaks, delete the application in the API add-on, make a new one, and paste it under **Configure → API connection**.
>
> The token acts as the admin who created it, so Home Assistant can clock in **every** employee.

## 2. Install

### From the Marketplace (recommended)

1. In Home Assistant, go to **Settings → Marketplace**.
2. Click **⋮** (top right) → **Custom repositories**.
3. Paste `https://github.com/powertbs/ha-quickbooks-time`, choose type **Integration**, and click **Add**.
4. Search for **QuickBooks Time** → **Download**.
5. **Restart Home Assistant** (Settings → System → ⏻ → Restart).

### Manually

Copy the `custom_components/tsheets` folder into your Home Assistant `config` folder, so you have
`/config/custom_components/tsheets/manifest.json`. Restart Home Assistant.

## 3. Connect your account

1. **Settings → Devices & services → + Add integration** → search **QuickBooks Time**.
2. Paste the **Access Token** from step 1 → **Submit**.

Home Assistant downloads your employees and jobs. One device, **TSheets**, appears with all the entities.

## 4. Settings (Configure)

On the integration, click **Configure**:

| Menu | What it's for |
|---|---|
| **API connection** | Shows which account is connected and lets you paste a new access token (for example after replacing the API application). |
| **Employee PINs** | Give each employee a 4-digit PIN for the time clock. Leave it blank for anyone who shouldn't use it. Duplicate PINs are rejected. |
| **Employee photos** | Upload a JPG/PNG/WebP photo per employee. Without one, their QuickBooks Time profile picture is used. |
| **Male / female** | Picks the placeholder picture (blue / pink) on the Who's In card for employees without a photo. |
| **General settings** | **Job for time-clock clock-ins:** by default a customer/job named **Office** if you have one, otherwise no job. You can pick any job here. Also sets how often to refresh (default 60 s) and how old a GPS point can be (default 120 min). |

> QuickBooks Time's API doesn't share the PINs set in QuickBooks Time itself, or employees' gender, so those are set here.

## 5. Add the cards to a dashboard

Open your dashboard → **✏️ Edit** → **+ Add card** → search **QuickBooks Time**. Pick a card, adjust the options in the editor, and click **Save**.

### QuickBooks Time Clock

A button that opens the PIN keypad in a pop-up.

- **On the PIN screen:** a big clock and 4 dots that fill as digits are typed.
- **Employee screen:** photo, name, and status ("You are clocked IN now · since 8:58 AM"), with **Clock In** / **Clock Out** buttons. The one that doesn't apply is grayed out.
- **After a punch:** a green or red confirmation, then the pop-up closes by itself.
- **When nobody is using it:** the pop-up closes after 8 seconds untouched.
- **Keyboard:** a physical keyboard works too: digits, Backspace, Esc.

```yaml
type: custom:tsheets-time-clock-card
# all optional:
name: Time Clock
subtitle: Clock in / out
icon: mdi:clock-check-outline
auto_close: 8          # seconds untouched before the pop-up closes (0 = never)
inline: false          # true = show the keypad right on the dashboard (wall tablet)
```

### QuickBooks Time Who's In

Everyone on the clock, sorted by who came in first. Each person shows their name, photo (or a placeholder), job, and first clock-in time today. If more people are in than fit, the row scrolls sideways. The card hides itself when nobody is on the clock.

```yaml
type: custom:tsheets-whos-in-card
# all optional:
title: Who's In        # leave out for no title
theme: auto            # auto (follows your theme) | light | dark (dark glass, for photo backgrounds)
hide_when_empty: true
show_job: true
show_time: true
```

## Entities

| Entity | What it shows |
|---|---|
| `sensor.tsheets_clocked_in` | How many people are on the clock. The `employees` attribute lists name, picture, gender, job, customer, shift start (`since`), and first clock-in today (`first_in`) |
| `sensor.tsheets_kiosk` | Time clock screen (`idle`, `typing`, `checking`, `employee`, `working`, `result`, `error`) and the employee on screen |
| `sensor.tsheets_keypad_display` | Keypad text: `Enter your PIN`, `●●●`, `✖ PIN not recognized` |
| `sensor.tsheets_last_punch` | The last clock in/out done from Home Assistant |
| `button.tsheets_clock_in` / `button.tsheets_clock_out` | The employee-screen buttons |
| `binary_sensor.<name>_on_the_clock` | On/off per employee, with job and customer |
| `sensor.<name>_current_job` | `Customer › Job`, or `Off the clock` |
| `device_tracker.<name>_location` | Latest GPS point from the QuickBooks Time mobile app (shown on the **Map**) |

## Services

| Service | Use |
|---|---|
| `tsheets.punch` | `pin: "1234"`, optional `action: toggle / in / out` and `jobcode_id` |
| `tsheets.clock_in` / `tsheets.clock_out` | By `user_id`, for example "clock everyone out at 7 PM" |
| `tsheets.keypad` | `key: 0-9 / back / clear / enter / cancel / clock_in / clock_out / open / wake`, which drives the keypad from your own buttons. `open` is for a browser_mod pop-up: the integration closes it (`browser_mod.close_popup`) after 8 s untouched. The built-in card sends `wake` and closes itself. |

## Events

- `tsheets_punch` fires on every successful punch, with `name`, `action`, `job`, `time` and `worked`.
- `tsheets_punch_failed` fires on a wrong PIN or an API error.
- `tsheets_kiosk_idle` fires when a browser_mod pop-up was closed for being idle.

Use them to play a sound, flash a light, or send a notification.

## Good to know

- **Wrong PINs:** after 5 wrong PINs in a row, the keypad locks for 60 seconds. A half-typed PIN clears after 20 seconds.
- **One shared screen:** the keypad screen is shared. Two tablets showing the time clock at the same moment see the same screen.
- **PINs aren't passwords:** PINs are stored in Home Assistant's config in plain text. They're meant to stop buddy-punching.
- **Photos are public URLs:** photos are served at `/tsheets_photos/…` without a login, like anything in HA's `www` folder.
- **Changes made in QuickBooks Time** (a phone clock-in, a manager's edit) show up on the next refresh.
- **GPS** only appears for employees using the QuickBooks Time mobile app with location tracking on.

## Troubleshooting

- **"QuickBooks Time rejected that token":** the token was copied incompletely, or the API application was deleted. Make a new one (step 1) and paste it under **Configure → API connection**.
- **The cards don't appear under "Add card":** restart Home Assistant once after installing, then refresh the browser (Ctrl+F5, or clear the companion app's cache).
- **Clock-in fails with a job error:** your QuickBooks Time requires a job. Pick one under **Configure → General settings**.

---

Not affiliated with or endorsed by Intuit. QuickBooks and TSheets are trademarks of Intuit Inc.

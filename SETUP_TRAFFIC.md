# Live traffic (optional)

Traffic Intelligence works without any setup: you can record conditions by hand.
To also fetch live readings near your restaurant, the app can use **TomTom's Traffic Flow API**.

1. Create a free developer account at https://developer.tomtom.com/ and create an API key
   with the **Traffic API** enabled.
2. Set your restaurant's latitude and longitude in **Settings → Restaurant**
   (point it at the road in front of the restaurant, not just the building).
3. Set the key before starting the server.

   Windows (PowerShell):
   ```
   $env:TOMTOM_API_KEY="your-key"
   python manage.py runserver
   ```
   macOS / Linux:
   ```
   export TOMTOM_API_KEY="your-key"
   python manage.py runserver
   ```
4. Open Traffic Intelligence and press **Fetch live reading**.
   Each press saves one timestamped reading (condition + current vs free-flow speed).

The app never invents traffic: with no key configured it says so, and readings
only exist when you enter them or press the button. Never share your key.

How the condition is chosen: current speed ÷ free-flow speed.
80% or more = Free flowing, 50–80% = Moderate, 25–50% = Heavy, under 25% = Standstill.

Note: this integration could not be tested against the live TomTom service while
it was built (no key/network access), only against a simulated response. If a
fetch fails, the page shows the reason.

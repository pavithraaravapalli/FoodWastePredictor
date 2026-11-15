# Email setup (forgot password, invitations)

Without configuration, emails are **printed in the terminal** where `runserver` is running
(look for "Subject: Reset your password"). That is fine for local development.

To send real email, set these environment variables before starting the server.

PowerShell (current window only):
```powershell
$env:EMAIL_HOST = "smtp.gmail.com"
$env:EMAIL_PORT = "587"
$env:EMAIL_HOST_USER = "you@gmail.com"
$env:EMAIL_HOST_PASSWORD = "your-16-char-app-password"   # a Gmail *App Password*, not your login password
$env:DEFAULT_FROM_EMAIL = "FoodWastePredictor <you@gmail.com>"
python manage.py runserver
```
Gmail needs 2-Step Verification on, then create an App Password at myaccount.google.com/apppasswords.
Never put these values in source code or share them.

Owners can also press the key icon next to any user in **Team** to send that person a password-reset link.

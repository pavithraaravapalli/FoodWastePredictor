"""Project-wide date/time formats.

Django's built-in English locale formats override settings.DATE_FORMAT
(that is why dates showed as "Dec. 31, 2026"), so the formats are set here,
through FORMAT_MODULE_PATH, where they take precedence.

Display:  DD-MM-YYYY (four-digit year), 24-hour time.
Input:    ISO (what <input type=date> submits) first, then DD-MM-YYYY typed by hand.
The FIRST input format is also the one used to render widget values, so it
must stay ISO for the native/JS date pickers.
"""
DATE_FORMAT = 'd-m-Y'
SHORT_DATE_FORMAT = 'd-m-Y'
DATETIME_FORMAT = 'd-m-Y H:i'
SHORT_DATETIME_FORMAT = 'd-m-Y H:i'
TIME_FORMAT = 'H:i'
MONTH_YEAR_FORMAT = 'F Y'
YEAR_MONTH_FORMAT = 'F Y'
MONTH_DAY_FORMAT = 'd-m'
FIRST_DAY_OF_WEEK = 1  # Monday

DATE_INPUT_FORMATS = ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y']
TIME_INPUT_FORMATS = ['%H:%M:%S', '%H:%M']
DATETIME_INPUT_FORMATS = [
    '%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
    '%d-%m-%Y %H:%M', '%d-%m-%Y %H:%M:%S',
]

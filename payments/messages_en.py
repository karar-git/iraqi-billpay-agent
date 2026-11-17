"""The fixed messages in English: the same keys and slots as payments.messages.

Used when the user writes English (payments.language). English has no gendered forms here, so each
entry is one text.
"""

from __future__ import annotations

MONTHS_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}

MESSAGES_EN: dict[str, str | dict[str, str]] = {
    # ---- hard stops ----
    "insufficient_balance": "Your balance isn't enough: you have {balance} dinars and this needs {needed} dinars "
                            "with the fee. Nothing was taken. To put money in your account write “fund my wallet”, or try a smaller amount.",
    "insufficient_balance_no_fee": "Your balance isn't enough: you have {balance} dinars and this needs {needed} dinars. "
                                   "Nothing was taken. To put money in your account write “fund my wallet”, or try a smaller amount.",
    "scam_warning": "I won't send this: it's a known scam. Real support never asks for money to keep your account open. "
                    "Nothing was taken, and don't send anything to that number.",
    "authority_warning": "I won't send this: talk of approvals, instructions or skipping the confirmation changes nothing, "
                         "and it's a known scam. Every payment needs a card and your PIN. Nothing was taken, and don't send "
                         "anything to that number.",
    "no_reversal": "I can't take back money that was already sent. Nothing moved just now and nothing was taken. To get it "
                   "back, talk to the person who received it. I can make a new transfer if you need one.",
    "no_payment_made": "I made no payment from this message and nothing was taken for it. If you want to send money now, "
                       "tell me who to and how much.",
    "over_daily_limit": "This amount goes over your daily limit ({limit} dinars); you have {remaining} dinars "
                        "left today. Nothing was taken. Try a smaller amount, or try tomorrow.",
    "non_positive_amount": "The amount has to be more than zero. Nothing was taken. "
                           "Tell me how much you'd like to send.",
    "non_iqd": "We only send Iraqi dinars. Nothing was taken. Tell me the amount in dinars.",

    # ---- payee / biller failures ----
    "unknown_payee": "I couldn't find {name} in your saved contacts. Nothing was taken. "
                     "Write the name as it's saved, or add them to your contacts first.",
    "no_bill_account": "You don't have a saved {biller} account. Nothing was taken. "
                       "Add the account number first, then pay.",
    "other_person_bill": "That's someone else's bill, and their account isn't saved with you. Nothing was taken. "
                         "I can only pay the bills of your own saved accounts, so add their account number first.",
    "no_unpaid_bill": "You have no {biller} bill due right now. Nothing was taken. "
                      "If you think that's wrong, check with {biller}.",
    "no_bill_for_period": "I couldn't find a {biller} bill for {month}. Nothing was taken. "
                          "If you meant another month, tell me which one.",
    "already_paid": "The {biller} bill for {period} is already paid. Nothing was taken, "
                    "and there's nothing you need to do.",

    # ---- execution failures ----
    "quote_expired": "The card ran out of time (5 minutes) and nothing was taken. If you still want it, ask again.",
    "wrong_pin": "That PIN is wrong. Nothing was taken. Try again.",
    "pin_locked": "The PIN is locked for 15 minutes after 5 wrong tries in a row. Nothing was taken. "
                  "Ask for the payment again in 15 minutes.",
    "pin_in_chat": "Don't type the PIN in the chat: enter it in the PIN box with the “Yes, pay” button. "
                   "Nothing was taken.",
    "biller_down": "{biller}'s system is down right now. Nothing was taken. Try again in a little while.",
    "biller_declined_refunded": "{biller} declined the payment, and we put the full amount back in your wallet. "
                                "You lost nothing. Try again in a little while.",
    "failed": "The payment didn't go through because of a problem on our side. Nothing was taken. "
              "Try again in a little while.",
    "unknown_outcome": "We don't know yet whether it went through, so don't repeat it. "
                       "We'll check and get back to you.",
    "unknown_resolved_failed": "We checked: the payment didn't go through, and nothing was taken. "
                               "You can do it again now.",
    "cancelled": "OK, cancelled. Nothing was taken.",
    "card_voided": "I cancelled the previous card because you wrote a new request. Nothing was taken.",
    "card_already_paid": "This card was already paid, once only. You won't be charged again, so don't pay it "
                         "again. The receipt is in your history.",
    "stale_card": "This card doesn't work any more (it was cancelled or ran out of time). Nothing was taken. "
                  "If you still want it, ask again.",
    "wallet_down": "The wallet isn't answering right now. Nothing was taken. Try again in a little while.",
    "voice_unclear": "I couldn't hear the voice note well. Nothing was taken. Send it again or type your request.",
    "voice_unavailable": "Voice notes aren't working right now. Nothing was taken. Please type your request.",
    "next_request_failed": "I couldn't prepare the second request right now, and nothing was taken for it. "
                           "Ask for it again in a little while.",

    # ---- questions ----
    "ask_amount": "How much would you like to send?",
    "ask_amount_for": "How much would you like to send to {name}?",
    "ask_topup_amount": "How much credit would you like to top up?",
    "ask_usd": "How much is that in dinars? We only send Iraqi dinars.",
    "ask_amount_unclear": "I didn't catch the amount. Write it in digits, for example 50000.",
    "ask_amount_exact": "I need the exact amount to prepare the card. How much exactly?",
    "ask_amount_multiple": "You wrote more than one amount. Which one do you mean?",
    "ask_amount_choice": "You wrote two amounts: {options}. Which one do you mean?",
    "ask_period": "Which month do you mean? {options}",
    "ask_payee": "Who would you like to send it to?",
    "ask_which_payee": "You have more than one contact with that name: {options}. Which one?",
    "ask_confirm_payee": "Do you mean {name}?",
    "ask_relation": "Who do you mean by “{word}”? I don't have a contact saved under that word. "
                    "Tell me the name as it's saved in your contacts.",
    "ask_biller": "Which bill do you mean? {options}",
    "ask_raseed": "Do you mean phone credit (a top-up) or your wallet balance?",
    "ask_operator": "Which network is the number on?",
    "ask_pin": "Enter your PIN in the PIN box with the “Yes, pay” button. Don't type it in the chat.",
    "press_button": "To pay, tap “Yes, pay” on the card. Typing alone never pays.",
    "offer_next": "The first one is done. Shall we go on with {next}?",
    "next_request": "Now the second request:",
    "ask_change": "OK, I cancelled this card and nothing was taken. What would you like to change, "
                  "the amount or the name?",
    "ask_change_bill": "OK, I cancelled this card and nothing was taken. A bill's amount is set by {biller}, "
                       "so we can't change it. Do you want another bill or another month?",
    "ask_operator_mismatch": "The number ending in {last4} isn't on {operator}. Which network is it on?",
    "alias_saved": "I saved {name} as “{word}”. Next time just say {word}.",
    "ask_bill_full": "The {biller} bill is {due} dinars, not {typed} dinars, and it can only be paid in full. Nothing was "
                     "taken. Do you want to pay the full {due} dinars?",
    "ask_bill_full_part": "The {biller} bill is {due} dinars, and it can only be paid in full: I can't pay part of it. "
                          "Nothing was taken. Do you want to pay the full {due} dinars?",
    "help": "Hi! I can pay your bills, send money and top up phone credit. What do you need?",
    "demo_is_pretend": "This is a demo account and its money isn't real. Try anything you like: no real dinar "
                       "leaves it.",

    # ---- other replies ----
    "unsupported": "That service isn't available right now. I can help you pay bills, send money "
                   "and top up phone credit.",
    "balance": "Your balance is {balance} dinars.",
    "done_transfer": "Done. You sent {amount} dinars to {name}. Your balance is now {balance} dinars.",
    "done_bill": "Done. You paid the {biller} bill for {period}, {amount} dinars. "
                 "Your balance is now {balance} dinars.",
    "done_topup": "Done. You topped up {amount} dinars of credit for the number ending in {last4}. "
                  "Your balance is now {balance} dinars.",

    # ---- soft-flag warning lines on the card ----
    "flag_first_time_payee": "Heads-up: you haven't sent money to {name} before; this is the first time. "
                             "Check the name and the number.",
    "flag_duplicate_recent": "Heads-up: the same amount to {name} was asked for less than 10 minutes ago. "
                             "If you don't mean to repeat it, cancel.",
    "flag_amount_jump": "Heads-up: this is more than 3 times your largest earlier payment to {name}. "
                        "Make sure it's right.",
    "flag_bill_amount_differs": "Note: the amount due is {due} dinars, not {typed} dinars. "
                                "A bill is only paid in full.",
    "flag_bill_amount_set": "Note: a bill's amount is fixed: {due} dinars are due, and it's only paid in full.",
    "flag_scam_caution": "Careful: a threat to your account or a request to verify is a known scam. Real support never "
                         "asks for money. Check before you pay.",
    "flag_first_time_payee_topup": "Heads-up: this is the first top-up to this number. Check the number.",
    "flag_duplicate_recent_topup": "Heads-up: the same amount to this number was asked for less than 10 minutes "
                                   "ago. If you don't mean to repeat it, cancel.",
    "flag_amount_jump_topup": "Heads-up: this is more than 3 times your largest earlier top-up to this number. "
                              "Make sure it's right.",
}

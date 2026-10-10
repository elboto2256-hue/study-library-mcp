# Sessions: original sample study note

Authentication establishes who a user is. Authorization checks which actions
that user may perform. A session lets a web application connect later requests
to the authenticated user.

A session identifier should be unpredictable. Renew it after login and changes
to privilege. Set an appropriate expiry and invalidate the session on logout.
Cookie settings help protect the identifier: Secure restricts transmission to
HTTPS, HttpOnly blocks JavaScript access, and SameSite limits certain cross-site
requests. These settings support, but do not replace, authorization checks.

Practice: in a fictional invoice application, Alice and Bob each own one invoice.
Explain why having a valid session must not let Alice read Bob's invoice simply
by changing an invoice identifier.

This is an original learning example, not an excerpt or summary of any book.

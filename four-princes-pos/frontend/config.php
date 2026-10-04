<?php
// Points to wherever the Flask backend is running. Locally this defaults to
// "python app.py" on the standard Flask port, unchanged from before. For a
// Vercel deployment, set an API_BASE_URL environment variable in the
// frontend project's settings (e.g. https://your-backend.vercel.app/api) --
// no code edit needed, this picks it up automatically.
define('API_BASE_URL', getenv('API_BASE_URL') ?: 'http://localhost:5000/api');

define('SHOP_NAME', 'Four Princes');
define('SHOP_ADDRESS', 'Rizal Street, Barangay Sabang I, Allen, Northern Samar');

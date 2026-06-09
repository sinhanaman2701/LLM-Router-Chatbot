const express = require('express');
const cookieParser = require('cookie-parser');
const multer = require('multer');
const cors = require('cors');

const app = express();
const port = 3000;

const SESSIONS = new Map();

const USERS = {
  'dn.user.a@gmail.com': { user_id: 'u1', name: 'Naman', email: 'dn.user.a@gmail.com', community_id: 'c1', unit_id: 'flat101', role: 'resident' },
  'test@example.com': { user_id: 'u2', name: 'Test User', email: 'test@example.com', community_id: 'c1', unit_id: 'flat102', role: 'resident' }
};

const FACILITIES = [
  { id: 'fac_1', name: 'Tennis Court', category: 'Sports', default_duration_min: 60, open_time: '07:00', close_time: '22:00' },
  { id: 'fac_2', name: 'Swimming Pool', category: 'Recreation', default_duration_min: 60, open_time: '07:00', close_time: '21:00' },
  { id: 'fac_3', name: 'Badminton Court', category: 'Sports', default_duration_min: 60, open_time: '07:00', close_time: '22:00' },
  { id: 'fac_4', name: 'Gym', category: 'Fitness', default_duration_min: 90, open_time: '06:00', close_time: '22:00' }
];

const formatDate = (date) => date.toISOString().split('T')[0];
const today = new Date();
const tomorrow = new Date(today);
tomorrow.setDate(today.getDate() + 1);
const dayAfter = new Date(today);
dayAfter.setDate(today.getDate() + 2);

const BOOKINGS = {
  fac_1: [
    { booking_id: 'bk_seed_1', date: formatDate(tomorrow), start_time: '09:00', end_time: '10:00', user: 'dn.user.a@gmail.com', status: 'Confirmed' },
    { booking_id: 'bk_seed_2', date: formatDate(dayAfter), start_time: '14:00', end_time: '15:00', user: 'test@example.com', status: 'Confirmed' }
  ],
  fac_2: [
    { booking_id: 'bk_seed_3', date: formatDate(tomorrow), start_time: '10:00', end_time: '11:00', user: 'test@example.com', status: 'Confirmed' },
    { booking_id: 'bk_seed_4', date: formatDate(dayAfter), start_time: '15:00', end_time: '16:00', user: 'dn.user.a@gmail.com', status: 'Confirmed' }
  ],
  fac_3: [
    { booking_id: 'bk_seed_5', date: formatDate(tomorrow), start_time: '18:00', end_time: '19:00', user: 'dn.user.a@gmail.com', status: 'Confirmed' }
  ],
  fac_4: []
};

const getAllBookingsForUser = (userEmail) => {
  const bookings = [];
  for (const facility of FACILITIES) {
    const facilityBookings = BOOKINGS[facility.id] || [];
    for (const booking of facilityBookings) {
      if (booking.user === userEmail && booking.status !== 'Cancelled') {
        bookings.push({
          ...booking,
          facility_id: facility.id,
          facility_name: facility.name,
          category: facility.category
        });
      }
    }
  }
  return bookings;
};

app.use(cors({
    origin: true,
    credentials: true
}));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(cookieParser());

// Multer for parsing multipart/form-data
const upload = multer();

// Dummy response template
const successResponse = (data = "", message = null) => ({
  "m_system_status_code": 0,
  "m_system_status_message": message,
  "m_app_response": {
    "m_response_data": data,
    "m_app_status_code": 0,
    "m_app_status_msg": null
  }
});

const errorResponse = (systemMsg = "Error", systemCode = 1) => ({
  "m_system_status_code": systemCode,
  "m_system_status_message": systemMsg,
  "m_app_response": {
    "m_response_data": null,
    "m_app_status_code": 1,
    "m_app_status_msg": "Application Error"
  }
});

// Middleware to check session cookie
const authMiddleware = (req, res, next) => {
  const sessionCookie = req.cookies.session_token;
  if (!sessionCookie) {
    return res.status(401).json(errorResponse("Unauthorized. Missing session cookie.", 401));
  }
  const userEmail = SESSIONS.get(sessionCookie);
  if (!userEmail) {
    return res.status(401).json(errorResponse("Unauthorized. Invalid session cookie.", 401));
  }
  req.user_email = userEmail;
  next();
};

// ----------------- AUTH CONTROLLER ----------------- //

// m_login
app.post('/auth/m_login', upload.none(), (req, res) => {
  const { email, password } = req.body;
  
  if (email && password) {
    const user = USERS[email];
    const responseData = user ? {
      user_id: user.user_id,
      name: user.name,
      user_email: user.email,
      community_id: user.community_id,
      unit_id: user.unit_id,
      role: user.role,
      bearer_token: ''
    } : {
      user_id: 'guest',
      name: 'Guest',
      user_email: email,
      community_id: 'c1',
      unit_id: 'unknown',
      role: 'guest',
      bearer_token: ''
    };

    const sessionToken = 'mock_session_token_' + Date.now();
    SESSIONS.set(sessionToken, responseData.user_email);
    res.cookie('session_token', sessionToken, {
      httpOnly: true
    });
    return res.json(successResponse(responseData));
  }

  return res.status(400).json(errorResponse("Invalid credentials", 400));
});

// m_logout
app.post('/auth/m_logout', upload.none(), (req, res) => {
  const sessionCookie = req.cookies.session_token;
  if (sessionCookie) {
    SESSIONS.delete(sessionCookie);
  }
  res.clearCookie('session_token');
  return res.json(successResponse());
});

app.get('/health', (req, res) => {
  return res.json({ status: 'ok' });
});


// ----------------- FACILITIES CONTROLLER ----------------- //

// m_get_facility_list
app.post('/facilities/m_get_facility_list', authMiddleware, upload.none(), (req, res) => {
  return res.json(successResponse(FACILITIES));
});

// m_get_facility_booking_data (handles dynamic paths)
app.post('/facilities/m_get_facility_booking_data/:id', authMiddleware, upload.none(), (req, res) => {
  const facilityId = req.params.id;
  const data = {
    facility_id: facilityId,
    bookings: BOOKINGS[facilityId] || []
  };
  return res.json(successResponse(data));
});

// m_directory (handles GET and POST just in case)
app.get('/facilities/m_directory', authMiddleware, (req, res) => {
  const data = { directory: ["Sports", "Recreation", "Meeting Rooms"] };
  return res.json(successResponse(data));
});

app.post('/facilities/m_directory', authMiddleware, upload.none(), (req, res) => {
  const data = { directory: ["Sports", "Recreation", "Meeting Rooms"] };
  return res.json(successResponse(data));
});

// m_get_facility_directory
app.post('/facilities/m_get_facility_directory', authMiddleware, upload.none(), (req, res) => {
  const data = {
    categories: ["eNortjK2UjI2NVOyBlwwDYACOA~~"]
  };
  return res.json(successResponse(data));
});

// m_get_facility_bookings_info
app.post('/facilities/m_get_facility_bookings_info', authMiddleware, upload.none(), (req, res) => {
  const data = {
    info: "Facility bookings information retrieved successfully."
  };
  return res.json(successResponse(data));
});

// m_get_my_bookings_v3
app.post('/facilities/m_get_my_bookings_v3', authMiddleware, upload.none(), (req, res) => {
  const userBookings = getAllBookingsForUser(req.user_email);
  const todayIso = formatDate(new Date());
  const data = {
    upcoming_bookings: userBookings.filter((booking) => booking.date >= todayIso),
    past_bookings: userBookings.filter((booking) => booking.date < todayIso)
  };
  return res.json(successResponse(data));
});

// m_member_make_booking
app.post('/facilities/m_member_make_booking', authMiddleware, upload.none(), (req, res) => {
  const { facility_id, date, start_time, end_time, user_email } = req.body;
  const booking_id = 'bk_' + Date.now();
  const booking = {
    booking_id,
    date,
    start_time,
    end_time,
    user: user_email,
    status: 'Confirmed'
  };

  if (!BOOKINGS[facility_id]) {
    BOOKINGS[facility_id] = [];
  }

  BOOKINGS[facility_id].push(booking);

  const data = {
    booking_id,
    facility_id,
    date,
    start_time,
    end_time,
    status: 'Confirmed'
  };
  return res.json(successResponse(data));
});

// m_cancel_booking
app.post('/facilities/m_cancel_booking', authMiddleware, upload.none(), (req, res) => {
  const { booking_id } = req.body;
  let cancelledBooking = null;

  for (const facilityId of Object.keys(BOOKINGS)) {
    const booking = (BOOKINGS[facilityId] || []).find((item) => item.booking_id === booking_id);
    if (booking) {
      booking.status = 'Cancelled';
      cancelledBooking = { ...booking, facility_id: facilityId };
      break;
    }
  }

  if (!cancelledBooking) {
    return res.status(404).json(errorResponse("Booking not found", 404));
  }

  const data = {
    cancelled_booking_id: booking_id,
    status: "Cancelled"
  };
  return res.json(successResponse(data));
});


// ----------------- COMMUNITY V2 CONTROLLER ----------------- //

// m_get_dashboard_static_data
app.post('/community_v2/m_get_dashboard_static_data', upload.none(), (req, res) => {
  const data = {
    static_data_version: "v1.0",
    widgets: []
  };
  return res.json(successResponse(data));
});

// m_sync_static_data
app.post('/community_v2/m_sync_static_data', upload.none(), (req, res) => {
  const data = {
    sync_status: "Completed",
    timestamp: Date.now()
  };
  return res.json(successResponse(data));
});


// Fallback for undefined routes
app.use((req, res) => {
  res.status(404).json(errorResponse(`Endpoint ${req.method} ${req.url} not found on dummy server`, 404));
});

app.listen(port, () => {
  console.log(`Mock server is running on http://localhost:${port}`);
});

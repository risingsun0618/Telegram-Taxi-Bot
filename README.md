# Telegram Rideshare Matching Bot

A button-driven Telegram bot for matching riders with drivers. All interactions use clickable buttons - no text commands needed.

## Features

- **User Registration**: Passengers and drivers register with name, phone, and ID document
- **Admin Approval**: All registrations require admin approval before users can use the service
- **Request Ride**: Riders share pickup/dropoff locations via GPS, select time, and specify passenger count
- **Offer Ride**: Drivers share start/end locations, departure time, and available seats
- **Automatic Matching**: Proximity-based matching (configurable radius) with time window compatibility
- **Contact Sharing**: Both parties receive full contact info (name, phone, Telegram) on match
- **Admin Reports**: Registration stats, trip reports, waiting times, seat utilization
- **Cancel Anytime**: Users can cancel their active requests/offers

## Setup

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure the Bot

Edit `config.py` to update:

- `BOT_TOKEN`: Your Telegram bot token from @BotFather
- `ADMIN_IDS`: List of Telegram user IDs who can approve registrations and view reports
- `MATCH_RADIUS_KM`: Maximum distance for matching (default: 5 km)
- `MATCH_TIME_WINDOW_MINUTES`: Time flexibility for matching (default: 30 minutes)

**To find your Telegram user ID**: Run the bot and send `/myid` command.

### 3. Run the Bot

```bash
python bot.py
```

For production, use a process manager like systemd or supervisor.

## File Structure

```
├── bot.py           # Main bot logic and handlers
├── config.py        # Configuration settings
├── database.py      # SQLite database operations
├── matching.py      # Matching algorithm
├── documents/       # Uploaded ID documents (auto-created)
├── requirements.txt
└── README.md
```

## How It Works

### Registration Flow

**Passenger Registration:**
1. Tap "Register"
2. Select "Register as Passenger"
3. Enter full name
4. Enter phone number
5. Select document type (ID Card / Driving License / Passport)
6. Upload photo of document
7. Wait for admin approval

**Driver Registration:**
1. Tap "Register"
2. Select "Register as Driver"
3. Enter full name
4. Enter phone number
5. Select document type
6. Upload photo of document
7. Select vehicle type (Car / SUV / Van / Minibus)
8. Select number of seats
9. Enter vehicle year and model
10. Wait for admin approval

### Rider Flow (after approval)
1. Tap "Request Ride"
2. Share pickup location (GPS)
3. Share drop-off location (GPS)
4. Select preferred time from buttons
5. Select passenger count
6. Wait for match notification

### Driver Flow (after approval)
1. Tap "Offer Ride"
2. Share starting location (GPS)
3. Share destination (GPS)
4. Select departure time from buttons
5. Select available seats
6. Wait for match notification

### Admin Panel
- **Pending Registrations**: Review and approve/reject new users
- **View Documents**: See uploaded ID documents
- **Registration Report**: Pending/approved/rejected counts, driver/passenger totals
- **Trip Report**: Matches, active trips, waiting riders, available drivers
- **Waiting Time Report**: Average wait times
- **Seat Utilization Report**: Seats offered vs filled

### Matching Logic
- Pickup must be within MATCH_RADIUS_KM of driver's start
- Drop-off must be within MATCH_RADIUS_KM of driver's end
- Times must be within MATCH_TIME_WINDOW_MINUTES
- Driver must have enough seats for passengers

### Match Notifications
When matched, both parties receive:
- Name and phone number
- Telegram username (if available)
- Ride time and seat/passenger info
- Driver also receives rider's pickup and dropoff locations as map pins

## Running as a Service (systemd)

Create `/etc/systemd/system/rideshare-bot.service`:

```ini
[Unit]
Description=Telegram Rideshare Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable rideshare-bot
sudo systemctl start rideshare-bot
```

## Commands

- `/start` - Show main menu
- `/myid` - Show your Telegram user ID (for admin setup)

## Customization

### Adding Admin Users
1. Have the user send `/myid` to the bot
2. Add their user ID to `ADMIN_IDS` list in `config.py`
3. Restart the bot

### Adding New Time Slots
Edit the `TIME_SLOTS` list in `bot.py`.

### Adding Vehicle Types
Edit the `VEHICLE_TYPES` list in `bot.py`.

### Adjusting Matching Parameters
Update values in `config.py` - no code changes required.

## Database

Uses SQLite (`rideshare.db`) with these tables:
- `users`: Registered users (drivers and passengers)
- `riders`: Active ride requests
- `drivers`: Active ride offers
- `matches`: Completed matches history
- `trips`: Trip records for multi-passenger rides
- `trip_passengers`: Links passengers to trips

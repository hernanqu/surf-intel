from flask import Flask, jsonify, render_template
import requests
import time

app = Flask(__name__)

# Simple in-memory cache to avoid excessive upstream API requests.
MARINE_CACHE = {
    "data": None,
    "timestamp": 0,
}

CACHE_TTL = 600  # 10 minutes

# --------------------------------------------------
# SURFER PROFILE
# --------------------------------------------------

SURFER = {
    "board": '6\'8" Haydenshape Hypto Krypto Soft',
    "volume_liters": 52,
    "level": "beginner / progressing intermediate",
    "objective": "skill progression",
}

# --------------------------------------------------
# SPOT
# --------------------------------------------------

VENICE = {
    "name": "Venice Breakwater",
    "latitude": 33.9832,
    "longitude": -118.4743,
    "wind_profile": {
        "offshore": [(45, 135)],
        "onshore": [(225, 315)],
    },
}

OPEN_METEO_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


# --------------------------------------------------
# MARINE DATA
# --------------------------------------------------

HOURLY = ",".join([
    "wave_height",
    "wave_direction",
    "wave_period",
    "wave_peak_period",
    "wind_wave_height",
    "wind_wave_direction",
    "wind_wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "secondary_swell_wave_height",
    "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "tertiary_swell_wave_height",
    "tertiary_swell_wave_direction",
    "tertiary_swell_wave_period",
    "sea_surface_temperature",
])

CURRENT = ",".join([
    "wave_height",
    "wave_direction",
    "wave_period",
    "wave_peak_period",
    "wind_wave_height",
    "wind_wave_direction",
    "wind_wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "secondary_swell_wave_height",
    "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "tertiary_swell_wave_height",
    "tertiary_swell_wave_direction",
    "tertiary_swell_wave_period",
    "sea_surface_temperature",
])


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def meters_to_feet(meters):
    if meters is None:
        return None
    return round(meters * 3.28084, 1)


def celsius_to_fahrenheit(celsius):
    if celsius is None:
        return None
    return round((celsius * 9 / 5) + 32, 1)


def compass_direction(degrees):
    if degrees is None:
        return "Unknown"

    directions = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW"
    ]

    index = round(degrees / 22.5) % 16
    return directions[index]


def classify_wind(wind_direction, wind_profile):
    if wind_direction is None:
        return "UNKNOWN"

    direction = wind_direction % 360

    for start, end in wind_profile.get("offshore", []):
        if start <= direction <= end:
            return "OFFSHORE"

    for start, end in wind_profile.get("onshore", []):
        if start <= direction <= end:
            return "ONSHORE"

    return "CROSS-SHORE"


# --------------------------------------------------
# SURF DECISION ENGINE
# --------------------------------------------------

def score_wave_size(height_ft):
    """
    Score based on suitability for a 6'8", ~52L softboard
    and a progressing surfer.

    This is deliberately conservative.
    """

    if height_ft is None:
        return 50

    if height_ft < 1.5:
        return 45

    if height_ft < 2.0:
        return 65

    if height_ft < 3.0:
        return 90

    if height_ft < 4.0:
        return 78

    if height_ft < 5.0:
        return 55

    if height_ft < 6.0:
        return 30

    return 10


def score_period(period_s):
    """
    Longer period generally means more organized wave energy.
    But very long-period swell can become powerful quickly.
    """

    if period_s is None:
        return 50

    if period_s < 7:
        return 40

    if period_s < 9:
        return 65

    if period_s < 12:
        return 90

    if period_s < 14:
        return 75

    return 55


def score_combination(height_ft, period_s):
    """
    Penalize combinations that can become disproportionately
    powerful for the current surfer profile.
    """

    if height_ft is None or period_s is None:
        return 50

    # Small + short period = weak/poor progression
    if height_ft < 2.0 and period_s < 8:
        return 45

    # Moderate size + moderate period = ideal zone
    if 2.0 <= height_ft < 4.0 and 8 <= period_s <= 12:
        return 90

    # Larger waves with longer periods become increasingly serious
    if height_ft >= 4.0 and period_s >= 12:
        return 40

    if height_ft >= 5.0:
        return 25

    return 65


def make_assessment(current):

    wave_height_ft = meters_to_feet(
        current.get("wave_height")
    )

    wave_period_s = current.get("wave_period")

    wave_direction = current.get("wave_direction")

    wind_speed_kmh = current.get(
        "wind_speed_10m"
    )

    wind_direction = current.get(
        "wind_direction_10m"
    )

    is_day = current.get("is_day")

    wind_speed_kt = (
        round(wind_speed_kmh * 0.539957, 1)
        if wind_speed_kmh is not None
        else None
    )

    wind_quality = classify_wind(
        wind_direction,
        VENICE.get("wind_profile", {})
    )

    if wind_speed_kt is None:
        wind_penalty = 0
    elif wind_speed_kt >= 16:
        wind_penalty = 100
    elif wind_quality == "OFFSHORE":
        if wind_speed_kt <= 7:
            wind_penalty = -5
        elif wind_speed_kt <= 11:
            wind_penalty = -2
        else:
            wind_penalty = 0
    elif wind_quality == "ONSHORE":
        if wind_speed_kt <= 4:
            wind_penalty = 5
        elif wind_speed_kt <= 7:
            wind_penalty = 12
        elif wind_speed_kt <= 11:
            wind_penalty = 20
        else:
            wind_penalty = 35
    else:
        if wind_speed_kt <= 4:
            wind_penalty = 0
        elif wind_speed_kt <= 7:
            wind_penalty = 3
        elif wind_speed_kt <= 11:
            wind_penalty = 8
        else:
            wind_penalty = 15

    if wind_speed_kt is None:
        wind_label = "UNKNOWN"
    elif wind_speed_kt >= 16:
        wind_label = "BLOWN TF OUT"
    elif wind_quality == "ONSHORE":
        wind_label = "ONSHORE"
    elif wind_quality == "OFFSHORE":
        wind_label = "OFFSHORE"
    else:
        wind_label = "CROSS-SHORE"

    size_score = score_wave_size(wave_height_ft)

    period_score = score_period(wave_period_s)

    combination_score = score_combination(
        wave_height_ft,
        wave_period_s
    )

    # Weighted score

    score = round(
        (size_score * 0.40)
        + (period_score * 0.25)
        + (combination_score * 0.35)
    )

    # Apply directional wind influence
    score = max(0, min(100, score - wind_penalty))

    # Hard safety limits
    if is_day == 0:
        status = "NO-GO"
    elif wind_speed_kt is not None and wind_speed_kt >= 16:
        status = "NO-GO"
    elif (
        wave_height_ft is not None
        and wave_period_s is not None
        and wave_height_ft >= 2
        and wave_period_s >= 16
    ):
        status = "NO-GO"
    elif (
        wave_height_ft is not None
        and wave_period_s is not None
        and wave_height_ft >= 3
        and wave_period_s >= 14
    ):
        status = "NO-GO"
    elif wave_height_ft is not None and wave_height_ft >= 6:
        status = "NO-GO"
    elif wave_height_ft is not None and wave_height_ft >= 5:
        status = "NO-GO"
    elif score >= 75:
        status = "GO"
    else:
        status = "NO-GO"

    # Explanation
    if is_day == 0:
        reason = "It's dark. Check back after dawn."
    elif status == "NO-GO" and wind_speed_kt is not None and wind_speed_kt >= 16:
        reason = (
            "Wind is too strong and is degrading the surface conditions. "
            "The waves may be surfable, but the overall conditions are not worth the session."
        )
    elif status == "NO-GO" and wind_quality == "ONSHORE":
        reason = (
            "Onshore wind is making the surface too messy "
            "for a productive session."
        )
    elif (
        status == "NO-GO"
        and wave_height_ft is not None
        and wave_period_s is not None
        and (
            (wave_height_ft >= 2 and wave_period_s >= 16)
            or (wave_height_ft >= 3 and wave_period_s >= 14)
        )
    ):
        reason = (
            "Long-period swell is making the surf more powerful "
            "than the wave height suggests."
        )
    elif status == "NO-GO" and wave_height_ft is not None and wave_height_ft >= 5:
        reason = (
            "Wave size is outside the preferred range. "
            "The surf is too powerful for a productive session."
        )
    elif status == "NO-GO" and wave_height_ft is not None and wave_height_ft < 2:
        reason = (
            "The wave field is too small for a productive session. "
            "There is not enough wave energy to make the most of the conditions."
        )
    elif status == "NO-GO" and wave_period_s is not None and wave_period_s < 8:
        reason = (
            "Wave energy is weak and inconsistent. "
            "The short period is limiting the quality of the surf."
        )
    elif status == "GO" and wind_quality == "OFFSHORE":
        reason = (
            "Offshore wind is helping clean up the surface, while wave size "
            "and energy remain within a workable range."
        )
    elif status == "GO" and wind_speed_kt is not None and wind_speed_kt <= 7:
        reason = (
            "Clean surface conditions with workable wave size and energy. "
            "Good conditions for a productive session."
        )
    elif status == "GO":
        reason = (
            "Wave size, energy, and surface conditions are workable. "
            "Good conditions for a productive session."
        )
    else:
        reason = (
            "Conditions are marginal. Wave size, energy, or surface quality "
            "is limiting the overall quality of the session."
        )

    return {
        "status": status,
        "score": score,
        "reason": reason,
        "wave_height_ft": wave_height_ft,
        "wave_period_s": (
            round(wave_period_s, 1)
            if wave_period_s is not None
            else None
        ),
        "wave_direction": wave_direction,
        "wave_compass": compass_direction(wave_direction),
        "size_score": size_score,
        "period_score": period_score,
        "combination_score": combination_score,
        "wind_speed_kt": wind_speed_kt,
        "wind_direction": wind_direction,
        "wind_compass": compass_direction(wind_direction),
        "wind_label": wind_label,
    }


# --------------------------------------------------
# API
# --------------------------------------------------

def get_marine_data():
    # Return cached data if it is still fresh.
    now = time.time()
    if (
        MARINE_CACHE["data"] is not None
        and now - MARINE_CACHE["timestamp"] < CACHE_TTL
    ):
        return MARINE_CACHE["data"]

    marine_params = {
        "latitude": VENICE["latitude"],
        "longitude": VENICE["longitude"],
        "hourly": HOURLY,
        "current": CURRENT,
        "timezone": "America/Los_Angeles",
        "forecast_days": 7,
    }

    marine_response = requests.get(
        OPEN_METEO_URL,
        params=marine_params,
        timeout=10
    )
    marine_response.raise_for_status()
    marine_data = marine_response.json()

    weather_params = {
        "latitude": VENICE["latitude"],
        "longitude": VENICE["longitude"],
        "current": "wind_speed_10m,wind_direction_10m,is_day",
        "timezone": "America/Los_Angeles",
    }

    try:
        weather_response = requests.get(
            OPEN_METEO_WEATHER_URL,
            params=weather_params,
            timeout=10
        )
        weather_response.raise_for_status()
        weather_data = weather_response.json()
        marine_data["wind"] = weather_data.get("current", {})
    except requests.RequestException:
        marine_data["wind"] = {}

    # Store the successful marine result.
    MARINE_CACHE["data"] = marine_data
    MARINE_CACHE["timestamp"] = now

    return marine_data


@app.route("/")
def index():
    return render_template(
        "index.html",
        spot=VENICE,
        surfer=SURFER
    )


@app.route("/api/marine")
def marine():

    data = get_marine_data()

    current = data.get("current", {})

    wind = data.get("wind", {})

    current["wind_speed_10m"] = wind.get(
        "wind_speed_10m"
    )

    current["wind_direction_10m"] = wind.get(
        "wind_direction_10m"
    )

    current["is_day"] = wind.get("is_day")

    assessment = make_assessment(current)

    water_temp_c = current.get(
        "sea_surface_temperature"
    )

    return jsonify({
        "spot": VENICE,
        "surfer": SURFER,
        "assessment": assessment,
        "water_temperature_f": celsius_to_fahrenheit(
            water_temp_c
        ),
        "current": current,
        "wind": wind,
    })


# --------------------------------------------------
# RUN
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)

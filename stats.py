from env import *
import sqlite3

# Environment Variables
env_source = open("env.json", "r")
env = json.load(env_source)
env_source.close()

EVENTS_DB_FILENAME = env['events']['eventsDbFilename']
DB_FILENAME = env['dbFilename']

def dbGetBotChannelIds():
    '''
    Returns the channel ids (cid) of every bot channel - a channel flagged "b": true in
    channels.json, mirrored into the single-row `channels` table. Activity in these channels
    is excluded from all post counts and stats.

    Returns [] if the channels row is missing, empty, or holds no bot channels, so a server
    with no bots configured behaves exactly as before.
    '''
    try:
        with sqlite3.connect(DB_FILENAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT channels FROM channels WHERE id = 1")
            row = cursor.fetchone()

        if not row or not row[0]:
            return []

        channels = json.loads(row[0])
        return [channel['cid'] for channel in channels.get('c', []) if channel.get('b')]

    except Exception:
        # Never let a stats read fail because of a missing or stale channels row - just don't exclude anything
        return []

def dbGetStats():
    result = {
        "h": {}, # individual Header stats
        "p": [], # array of Post stats
        "m": [], # array of Message stats
        "s": []  # array of Server stats
    }
        
    # Posts made in a bot channel are excluded from every post statistic. This resolves to a
    # no-op ("1 = 1") when no bot channels are configured, and to a NOT IN (...) list of cids
    # otherwise. cids come straight from the channels row and are forced to int, so the list
    # can never carry anything but integers into the SQL.
    bot_channel_ids = dbGetBotChannelIds()
    if bot_channel_ids:
        exclude_bot_channels = "json_extract(post, '$.cid') NOT IN (%s)" % ", ".join(str(int(cid)) for cid in bot_channel_ids)
    else:
        exclude_bot_channels = "1 = 1"

    unique_connecting_users_query = """
    SELECT
        COUNT(json_extract(user, '$.callsign')) as count
    FROM
        users
    WHERE
        CAST(json_extract(user, '$.last_connected') AS INTEGER) >= strftime('%s','now','localtime','-7 days')
        AND CAST(json_extract(user, '$.last_connected') AS INTEGER) < strftime('%s','now','localtime')
    """

    posts_select_query = f"""
    SELECT  
        1 as "Sort",
        "Posts" as "Category",
        "Total Posts" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE {exclude_bot_channels}
    UNION
    SELECT
        3 as "Sort",
        "Posts" as "Category",
        "Posts Today So Far" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE
        {exclude_bot_channels}
        AND CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day') * 1000
    UNION
    SELECT
        5 as "Sort",
        "Posts" as "Category",
        "Total Posts Last 7 Days" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE
        {exclude_bot_channels}
        AND CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-7 days') * 1000
        AND CAST(json_extract(post, '$.ts') AS INTEGER) < strftime('%s','now','localtime') * 1000
    UNION
    SELECT 
        8 as "Sort",
        "Posts" as "Category",
        "Top 5 Posters Last 7 Days" as "Statistic",
        GROUP_CONCAT(callsign || ': ' || count, ', ' ) as statistic
    FROM (SELECT  
            json_extract(post, '$.fc') as callsign,
            COUNT(json_extract(post, '$.ts')) as count
        FROM posts
        WHERE
            {exclude_bot_channels} AND
            CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-7 days') * 1000 AND
            CAST(json_extract(post, '$.ts') AS INTEGER) < strftime('%s','now','localtime') * 1000
        GROUP BY
            callsign
        ORDER BY 
            count DESC
        LIMIT  5)
    UNION
    SELECT  
        9 as "Sort",
        "Posts" as "Category",
        "Total Posts Last 30 Days" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE
        {exclude_bot_channels} AND
        CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-30 days') * 1000 AND
        CAST(json_extract(post, '$.ts') AS INTEGER) < strftime('%s','now','localtime') * 1000
    UNION
    SELECT
        10 as "Sort",
        "Posts" as "Category",
        "Most Posts in 1 Day" as "Statistic",
        GROUP_CONCAT(date || ': ' || postcount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', ROUND(json_extract(post, '$.ts') / 1000), 'unixepoch', 'localtime') AS date,
			COUNT(json_extract(post, '$.ts')) as postcount
        FROM posts
        WHERE {exclude_bot_channels}
        GROUP BY date
        ORDER BY postcount DESC
        LIMIT 1)
    UNION
    SELECT
        12 as "Sort",
        "Posts" as "Category",
        "Most Active Poster in 1 Day" as "Statistic",
        GROUP_CONCAT(callsign || ': ' || postcount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', ROUND(json_extract(post, '$.ts')/1000), 'unixepoch', 'localtime') AS date,
            COUNT(json_extract(post, '$.ts')) as postcount,
            json_extract(post, '$.fc') as callsign
        FROM posts
        WHERE
            {exclude_bot_channels}
            AND json_extract(post, '$.cid') != 6
        GROUP BY date, callsign
        ORDER BY postcount DESC
        LIMIT 1)
    ORDER BY 
        Category DESC, Sort ASC
    """

    messages_select_query = f"""
    SELECT  
        2 as "Sort",
        "Messages" as "Category",
        "Total Messages" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    UNION
    SELECT  
        3 as "Sort",
        "Messages" as "Category",
        "Messages Today So Far" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    WHERE 
        CAST(json_extract(message, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day')
    UNION
    SELECT  
        6 as "Sort",
        "Messages" as "Category",
        "Total Messages Last 7 Days" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    WHERE
        CAST(json_extract(message, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-7 days')
        AND CAST(json_extract(message, '$.ts') AS INTEGER) <  strftime('%s','now','localtime')
    UNION
    SELECT  
        7 as "Sort",
        "Messages" as "Category",
        "Total Messages Last 30 Days" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    WHERE
        CAST(json_extract(message, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-30 days')
        AND CAST(json_extract(message, '$.ts') AS INTEGER) <  strftime('%s','now','localtime')
    UNION
    SELECT
        9 as "Sort",
        "Messages" as "Category",
        "Most Messages in 1 Day" as "Statistic",
        GROUP_CONCAT(date || ': ' || messagecount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', json_extract(message, '$.ts'), 'unixepoch', 'localtime') AS date,
            COUNT(json_extract(message, '$.ts')) as messagecount
        FROM messages
        GROUP BY date
        ORDER BY messagecount DESC
        LIMIT 1)
    UNION
    SELECT
        11 as "Sort",
        "Messages" as "Category",
        "Most Active Messager in 1 Day" as "Statistic",
        GROUP_CONCAT(callsign || ': ' || messagecount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', json_extract(message, '$.ts'), 'unixepoch', 'localtime') AS date,
            COUNT(json_extract(message, '$.ts')) as messagecount,
            json_extract(message, '$.fc') as callsign
        FROM messages
        GROUP BY date, callsign
        ORDER BY messagecount DESC
        LIMIT 1)
    ORDER BY 
        Category DESC, Sort ASC
    """

    server_select_query = f"""
    SELECT 
        1 as "Sort",
        "Server" as "Category",
        "Bytes Sent Today So Far" as "Statistic",
        IFNULL(sum(json_extract(event, '$.e.bytes')), 0) AS count
    FROM 
        events
    WHERE
        json_extract(event, '$.et') = 'WPS_SEND' AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day') * 1000
    UNION
    SELECT 
        2 as "Sort",
        "Server" as "Category",
        "Bytes Sent Previous 7 Days" as "Statistic",
        sum(json_extract(event, '$.e.bytes')) AS count
    FROM 
        events
    WHERE
        json_extract(event, '$.et') = 'WPS_SEND' AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-7 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime') * 1000
    UNION
    SELECT 
        3 as "Sort",
        "Server" as "Category",
        "Bytes Sent Previous 30 Days" as "Statistic",
        sum(json_extract(event, '$.e.bytes')) AS count
    FROM 
        events
    WHERE
        json_extract(event, '$.et') = 'WPS_SEND' AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-30 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime') * 1000
    UNION
	SELECT  
		4 as "Sort",
		"Server" as "Category",
		"WPS Responses Sent Today So Far" as "Statistic",
		count(json_extract(event, '$.ts')) AS send_count
	FROM events
	WHERE
		json_extract(event, '$.et') = 'WPS_SEND' AND
		CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day') * 1000
	UNION
    SELECT  
        5 as "Sort",
        "Server" as "Category",
        "WPS Responses Sent Previous 7 Days" as "Statistic",
        count(json_extract(event, '$.ts')) AS count
    FROM events
    WHERE
        json_extract(event, '$.et') = 'WPS_SEND' AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-7 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime') * 1000
    UNION
    SELECT  
        6 as "Sort",
        "Server" as "Category",
        "WPS Responses Sent Previous 30 Days" as "Statistic",
        count(json_extract(event, '$.ts')) AS count
    FROM events
    WHERE
        json_extract(event, '$.et') = 'WPS_SEND' AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-30 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime') * 1000
    UNION
    SELECT
        7 as "Sort",
        "Server" as "Category",
        "Max Concurrent Users All Time" as "Statistic",
        (
            SELECT GROUP_CONCAT(peak_date || ': ' || peak_total, ', ')
            FROM (
                SELECT
                    json_extract(event, '$.e.total') AS peak_total,
                    strftime(
                        '%d-%m-%Y',
                        ROUND(json_extract(event, '$.ts') / 1000),
                        'unixepoch',
                        'localtime'
                    ) AS peak_date
                FROM events
                WHERE json_extract(event, '$.et') = 'USER_CONNECT'
                ORDER BY
                    json_extract(event, '$.e.total') DESC,
                    json_extract(event, '$.ts') DESC
                LIMIT 1
            )
        ) AS count
    UNION
    SELECT
        8 as "Sort",
        "Server" as "Category",
        "Max Concurrent Users Last 7 Days" as "Statistic",
        MAX(json_extract(event, '$.e.total')) AS count
    FROM events
    WHERE
        json_extract(event, '$.et') = 'USER_CONNECT'
        AND CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','-7 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) < strftime('%s','now','localtime') * 1000
    ORDER BY
        Sort ASC
    """

    try:

        with sqlite3.connect(DB_FILENAME) as conn:
            cursor = conn.cursor()

            cursor.execute(unique_connecting_users_query)
            conn.commit()            
            
            for row in cursor:                
                result["h"]["uculsd"] = row[0]

            cursor.execute(posts_select_query)
            conn.commit()

            for row in cursor:                
                result["p"].append({ "s": row[2], "v": row[3] })

            cursor.execute(messages_select_query)
            conn.commit()

            for row in cursor:                
                result["m"].append({ "s": row[2], "v": row[3] })

        with sqlite3.connect(EVENTS_DB_FILENAME) as conn:
            cursor = conn.cursor()

            cursor.execute(server_select_query)
            conn.commit()

            for row in cursor:
                result["s"].append({ "s": row[2], "v": row[3] })
        
        return_success = {
            "result": "success",
            "data": result
        }
        
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetEventStats",
            "params": ''
        }
        
        return return_error
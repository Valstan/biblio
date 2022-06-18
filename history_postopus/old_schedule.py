from datetime import timedelta, datetime
from time import sleep

from config import cron_schedule


def old_schedule():
    min_interval = 1
    interval = 30

    while True:

        if min_interval > 20:
            min_interval -= 20

        sleep(min_interval)

        min_interval = 3 * 60 * 60

        timenow = datetime.now().time()
        timenow = timedelta(hours=timenow.hour, minutes=timenow.minute, seconds=timenow.second)
        timenow = timenow.seconds

        for string_schedule in cron_schedule:

            minute, hours_all, prefix = string_schedule.split()
            minute = int(minute)
            hours_all = hours_all.split(',')
            hours = []
            for hour in hours_all:
                if '-' in hour:
                    hour = hour.split('-')
                    hour = [i for i in range(int(hour[0]), int(hour[1]) + 1)]
                    hours.extend(hour)
                    continue
                hours.append(int(hour))
            for hour in hours:
                time_schedule = hour * 60 * 60 + minute * 60
                now_interval = abs(timenow - time_schedule)
                if now_interval < min_interval:
                    min_interval = now_interval
                if now_interval < interval:
                    start(prefix)
                    min_interval = 120


if __name__ == '__main__':
    schedule()

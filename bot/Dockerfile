FROM python:3.11

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./bot

CMD ["python", "-u", "-m", "bot.run_bot"]

FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
# --trusted-host：此網路的 HTTPS 有憑證攔截，容器內驗證會失敗
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt

COPY . .

CMD ["python", "-u", "main.py"]

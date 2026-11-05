FROM python:3.13-alpine
RUN apk add --no-cache nftables conntrack-tools dnsmasq arp-scan iproute2 iputils-ping font-dejavu \
 && mkdir -p /usr/share/netwatch \
 && (wget -q -T 30 -O /usr/share/netwatch/manuf https://www.wireshark.org/download/automated/data/manuf || true)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
ENV PYTHONUNBUFFERED=1 \
    WEBSOCKETS_MAX_LINE_LENGTH=65536
CMD ["python", "-m", "app.main"]

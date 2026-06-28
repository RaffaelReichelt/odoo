#FROM --platform=linux/amd64 bitnami/python:3.11-debian-12
# TODO: Environmentvariablen im Dockerfile
FROM python:3.11-trixie
ARG TARGETPLATFORM

USER root
RUN useradd -M odoo
RUN apt update && apt install libssl-dev libxml2-dev libxslt-dev libldap2-dev libpq-dev libsasl2-dev libxrender1 libfontconfig libssl3 libldap-common nano net-tools dnsutils fontconfig libfreetype6 libjpeg62-turbo libpng16-16 libxext6 libxrender1 xfonts-75dpi xfonts-base cron tar gzip -y

# Nun noch die extras für wkhtmltopdf
RUN apt install -y fontconfig libfreetype6 libjpeg62-turbo libpng16-16 libxext6 libxrender1 xfonts-75dpi xfonts-base cron tar gzip
# und für den Backup des filestore
RUN apt install -y cron tar gzip

#COPY ./Certificates/*.pem /usr/share/ca-certificates/
#RUN echo "_.rare.local.pem" >> /etc/ca-certificates.conf

#RUN update-ca-certificates
WORKDIR /usr/src/odoo
RUN if [ "$TARGETPLATFORM" = "linux/amd64" ]; then \
    wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb && \
    dpkg -i wkhtmltox_0.12.6.1-3.bookworm_amd64.deb; \
elif [ "$TARGETPLATFORM" = "linux/arm64" ]; then \
    wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_arm64.deb && \
    dpkg -i wkhtmltox_0.12.6.1-3.bookworm_arm64.deb; \
fi

RUN apt install -f -y
RUN pip3 install setuptools wheel
RUN pip3 install pdfminer.six
RUN pip3 install --upgrade pyOpenSSL

COPY ./community/requirements.txt requirements.txt
RUN echo "\nphonenumbers" >> requirements.txt
RUN pip3 install -r requirements.txt


#COPY ./Certificates/cacert.pem /opt/bitnami/python/lib/python3.11/site-packages/certifi/cacert.pem
# COPY ./Certificates/cacert.pem /usr/local/lib/python3.11/dist-packages/certifi/cacert.pem

# Erstellen der Backup-Verzeichnisse
# RUN mkdir -p /usr/src/odoo/home/backups && \
#     chown -R odoo:odoo /usr/src/odoo/home/backups


# Crontab für stündliche Backups einrichten
# RUN echo "0 * * * * /opt/scripts/backup_filestore.sh > /var/log/backup_cron.log 2>&1" > /etc/cron.d/filestore-backup \
#     && chmod 0644 /etc/cron.d/filestore-backup
# COPY crontab /etc/cron.d/filestore-backup
# RUN chmod 0644 /etc/cron.d/filestore-backup
# RUN crontab /etc/cron.d/filestore-backup
# Erstellen Sie die Log-Datei für Cron
# RUN touch /var/log/backup_cron.log && \
#     chmod 0644 /var/log/backup_cron.log
# Starten Sie den Cron-Dienst im Hintergrund
# RUN service cron start

# Erstellen Sie ein Verzeichnis für das PID-File (wichtig für cron)
# RUN mkdir -p /var/run/crond && \
#     chmod -R 0755 /var/run/crond

# Backup-Script erstellen
RUN mkdir -p /opt/scripts 

# Backup-Skripte kopieren und ausführbar machen
# COPY ./backup_filestore.sh /opt/scripts/backup_filestore.sh
COPY ./start.sh /opt/scripts/start.sh

# RUN chmod +x /opt/scripts/backup_filestore.sh /opt/scripts/start.sh
RUN chmod +x  /opt/scripts/start.sh
# Neuer Startbefehl, der das Skript ausführt
USER odoo
CMD ["/opt/scripts/start.sh"]
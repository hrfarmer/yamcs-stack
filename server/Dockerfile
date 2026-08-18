FROM maven:3.9.11-eclipse-temurin-17 AS builder

WORKDIR /build
COPY pom.xml ./
RUN mvn -B -ntp package \
    && archive="$(find target -maxdepth 1 -name '*.tar.gz' -print -quit)" \
    && test -n "$archive" \
    && mkdir /yamcs \
    && tar -xzf "$archive" -C /yamcs --strip-components=1

FROM eclipse-temurin:17-jre-jammy

RUN groupadd --gid 10001 yamcs \
    && useradd --uid 10001 --gid yamcs --home-dir /opt/yamcs --shell /usr/sbin/nologin yamcs \
    && mkdir -p /yamcs-config /yamcs-data /yamcs-cache \
    && chown -R yamcs:yamcs /yamcs-config /yamcs-data /yamcs-cache

COPY --from=builder --chown=yamcs:yamcs /yamcs /opt/yamcs
RUN chmod 0755 /opt/yamcs/bin/yamcsd \
    && chmod -R a+rX /opt/yamcs

USER yamcs
WORKDIR /opt/yamcs
EXPOSE 8090/tcp 50000/udp 50001/udp

ENTRYPOINT ["/opt/yamcs/bin/yamcsd"]
CMD ["--no-color", "--etc-dir", "/yamcs-config/etc", "--data-dir", "/yamcs-data", "--cache-dir", "/yamcs-cache"]

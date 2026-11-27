# frontend/Dockerfile
# Nginx для раздачи статики

FROM nginx:alpine

# Копируем HTML
COPY index.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/index.html
COPY auth.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/auth.html
COPY promo.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/promo.html
COPY --chmod=644 promo2.html promo2.css promo2.js /usr/share/nginx/html/
COPY --chmod=644 metrics.js /usr/share/nginx/html/
COPY privacy.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/privacy.html
COPY terms.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/terms.html
COPY admin.html /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/admin.html

# Копируем конфиг nginx
COPY icons/ /usr/share/nginx/html/icons/
RUN chmod 755 /usr/share/nginx/html/icons && chmod 644 /usr/share/nginx/html/icons/favicon.svg
COPY fonts/ /usr/share/nginx/html/fonts/
COPY img/ /usr/share/nginx/html/img/
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY robots.txt /usr/share/nginx/html/
COPY sitemap.xml /usr/share/nginx/html/
# Порт
EXPOSE 80

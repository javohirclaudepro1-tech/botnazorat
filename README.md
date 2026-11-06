# 📊 Bot Nazorat

Telegram botingizda kim **kirdi** (/start bosdi), kim **bloklab chiqib ketdi**, kim **faol** ishlatyapti — hammasini ko'rsatadigan bot.

## Nimalarni qiladi

| Tugma | Nima ko'rsatadi |
|---|---|
| 📊 Statistika | Jami, tirik, bloklagan (%), bugun/7/30 kunda kirganlar, faollar, chiqib ketganlar |
| 🆕 Kirganlar | Oxirgi kirganlar: ism, username, aniq vaqt, qaysi havoladan kelgani |
| ⛔ Bloklaganlar | Kim va qachon bloklagan, nechta harakat qilgandi |
| 🔥 Faollar | 7 kunda eng ko'p ishlatganlar (🥇🥈🥉) |
| 📣 Manbalar | `t.me/botingiz?start=insta1` kabi havolalar: qaysi reklamadan nechta odam kelgani |
| 📈 Grafik | 30 kunlik grafik rasm ko'rinishida |
| 📁 Excel | Barcha foydalanuvchilar va kunlik statistika (.xlsx) |
| 🔍 Bloklarni tekshirish | Ro'yxatdagi har bir odam botni bloklaganmi yoki yo'qmi |
| 📱 Panel | Chiroyli Mini App: grafiklar, ro'yxatlar, qidiruv |

Bundan tashqari:
- Kimdir botga kirsa yoki uni bloklasa, sizga **darhol xabar** keladi.
- Har kuni soat 21:00 da **kunlik hisobot** keladi.

## Qanday ishlaydi

Botingiz **webhook** rejimida ishlasa, u **🟢 to'liq kuzatuv** rejimida ishlaydi:

```
Telegram → Bot Nazorat serveri (statistikaga yozadi) → botingizning asl serveri
```

- Xabarlar asl serveringizga **o'zgarishsiz** yetib boradi, javoblar ham o'zgarmay qaytadi. Botingiz avvalgidek ishlayveradi.
- Asl kodingiz webhook'ni qayta o'rnatib qo'ysa, Bot Nazorat buni har 5 daqiqada tekshiradi va kuzatuvni o'zi tiklaydi.
- **❌ Uzish** tugmasini bossangiz, webhook asl manziliga qaytariladi.

Botingiz **polling** rejimida ishlasa, u **🟡 tekshiruv rejimi** da ishlaydi. Telegram polling'dagi botning xabarlarini ikki joyga bera olmaydi. Shuning uchun:
1. Foydalanuvchilar ID ro'yxatini (.txt yoki .csv) yuborasiz.
2. **🔍 Tekshirish** tugmasini bosasiz va kim bloklagani aniqlanadi.

> Tekshiruv paytida har bir foydalanuvchiga bir lahzalik "yozmoqda…" holati yuboriladi. Xabar yuborilmaydi.

---

## O'rnatish (bir marta, ~15 daqiqa)

### 1. Nazorat botini yarating
1. @BotFather → `/newbot` buyrug'ini yuboring. Nomini masalan "SpeakUp Nazorat" qo'ying.
2. Berilgan tokenni saqlab qo'ying. Bu **yangi** botning tokeni, SpeakUp'niki emas.
3. @userinfobot ga yozib, o'zingizning Telegram ID'ingizni bilib oling.

### 2. Serverga qo'ying

Bot kecha-kunduz ishlashi va **https** manzili bo'lishi kerak.

**Railway (eng oson):**
1. Bu papkani GitHub'ga yuklang (yopiq repo bo'lsa ham bo'ladi).
2. railway.com → **New Project** → **Deploy from GitHub repo** → shu repo'ni tanlang. Railway `Dockerfile`'ni o'zi topadi.
3. **Settings → Networking → Generate Domain** ni bosing. Masalan `https://botnazorat-production.up.railway.app` manzili chiqadi.
4. **Variables** bo'limiga `.env.example` dagi qiymatlarni kiriting:
   - `CONTROL_TOKEN`
   - `PUBLIC_URL` (3-qadamdagi manzil)
   - `SECRET_KEY`
   - `ALLOWED_USERS`
5. Bot o'chib-yonganda statistika o'chib ketmasligi uchun **Volume** qo'shing: Mount path = `/app/data`.
6. Deploy tugagach, nazorat botingizga `/start` yozing.

> Render.com'ning bepul tarifi to'g'ri kelmaydi: u 15 daqiqa ishlatilmasa uxlab qoladi va xabarlar yo'qoladi.

**Bepul variant — Oracle Cloud Always Free (Ubuntu):**
```bash
git clone https://github.com/SIZNING-LOGIN/botnazorat.git
cd botnazorat && sudo bash install.sh
```
Skript hammasini o'zi qiladi: Python, bepul HTTPS (sslip.io + Let's Encrypt), avtomatik ishga tushish. Oracle'da **Security List**'da 80 va 443 portlarini ochishni unutmang.

**O'z VPS serveringiz (Docker bilan):**
```bash
cp .env.example .env    # qiymatlarni to'ldiring
docker build -t botnazorat .
docker run -d --name botnazorat --restart=always --env-file .env -p 8080:8080 -v $PWD/data:/app/data botnazorat
```
Keyin domeningizni https bilan 8080-portga yo'naltiring. Masalan, Caddy bilan bitta qator kifoya: `nazorat.sayt.uz { reverse_proxy localhost:8080 }`.

**Docker'siz:**
```bash
pip install -r requirements.txt
cp .env.example .env    # to'ldiring
python -m botnazorat
```

### 3. Botingizni ulang
1. Nazorat botida **➕ Bot ulash** ni bosing.
2. @BotFather → `/mybots` → @speakupcefrbot → **API Token** ga kirib, tokenni nazorat botiga yuboring. Xabar darhol o'chiriladi va token shifrlangan holda saqlanadi.
3. Bot **maxfiy kalit (secret_token)** haqida so'raydi:
   - Kodingizda `secret_token` yoki `WEBHOOK_SECRET` bo'lmasa, **"Kalit yo'q"** ni bosing.
   - Bo'lsa, o'sha qiymatni yuboring.
   - Bilmasangiz, dasturchingizdan so'rang. Noto'g'ri tanlansa, bot ogohlantiradi va **↩️ Asl holatga qaytarish** tugmasini beradi.

### 4. (Ixtiyoriy) Panel tugmasi
Panel bot menyusida doim tursin desangiz:
1. @BotFather → `/mybots` → nazorat boti → **Bot Settings → Menu Button**.
2. URL sifatida `https://SIZNING-MANZIL/panel` ni kiriting.

---

## Eski foydalanuvchilar haqida
Kuzatuv ulangan paytdan boshlab yoziladi. Ulanishdan oldin kirganlarni ham ko'rmoqchi bo'lsangiz:
1. Botingiz bazasidan foydalanuvchilar ID ro'yxatini oling, odatda bu `users` jadvali.
2. Uni **📥 ID import** ga yuboring.
3. **🔍 Bloklarni tekshirish** ni bosing.

## Xavfsizlik
- Tokenlar `SECRET_KEY` bilan shifrlanadi. `SECRET_KEY`'ni o'zgartirsangiz, botlarni qayta ulashingizga to'g'ri keladi.
- `ALLOWED_USERS` to'ldirilgan bo'lsa, botdan faqat o'sha odamlar foydalana oladi.
- Har kim faqat o'zi ulagan botni ko'radi.
- Panel Telegram imzosi (initData) bilan himoyalangan: tashqaridan ochib bo'lmaydi.

## Fayllar
```
botnazorat/
  app.py        boshqaruv boti, webhook proxy, panel API, kunlik hisobot
  tracker.py    update'lardan kirish/bloklash/faollikni ajratib oladi
  reports.py    statistika, grafik, Excel
  db.py         SQLite baza
  panel.html    Mini App panel
tests/test_flow.py   to'liq sinov (python -m tests.test_flow)
```

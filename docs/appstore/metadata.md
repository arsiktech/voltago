# App Store metadata — en / es / ru

Status: **pre-publication template; not paste-ready or release-ready**. Owner decisions,
native-speaker review, implementation evidence, and final App Store Connect checks remain
open. Nothing here proves name availability or authorizes an external change.

Apple currently limits the localized name and subtitle to 30 characters, promotional
text to 170 characters, description to 4,000 characters, and keywords to 100 UTF-8
bytes. Support URL must lead to real contact information; a Privacy Policy URL is
required for iOS. See [App information](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information)
and [platform-version information](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information).

## Name candidates

Use one name consistently after checking the existing App Store Connect app record:

1. `Volta Carga Clara`
2. `Carga Clara España`
3. `Ruta de Carga España`

The check must happen inside App Store Connect. Search the existing account and bundle
ID `tech.arsik.volta` first; do not create a second app merely to test a name. If a name
is unavailable, move to the next candidate. Re-run the keyword-duplication review after
the final name is selected. App Store availability is not trademark clearance; the owner
must also complete a jurisdiction-appropriate name/mark review before publication.

## Shared URL fields — owner-gated

- Privacy Policy URL: `https://[OWNER-DOMAIN]/privacy`
- Support URL: `https://[OWNER-DOMAIN]/support`
- Terms URL used inside the app/paywall: `https://[OWNER-DOMAIN]/terms`
- Marketing URL, optional: `https://[OWNER-DOMAIN]/volta`

Do not paste these placeholders into App Store Connect. The privacy and support URLs
must be public, stable HTTPS pages; the support page must contain real contact
information. The final Privacy, Terms, and Support destinations must also be embedded in
the submitted binary through `VOLTA_PRIVACY_POLICY_URL`, `VOLTA_TERMS_URL`, and
`VOLTA_SUPPORT_URL`, resolving to `VoltaPrivacyPolicyURL`, `VoltaTermsURL`, and
`VoltaSupportURL` in the archive; every Settings/paywall link must open the same approved
public page.

## English (U.S. or U.K. storefront as selected by owner)

**Subtitle**

`EV charger evidence in Spain`

**Promotional text**

`Find free and low-cost charging across Spain, with source, freshness, parking and connector evidence shown before you route.`

**Description**

```text
Find free and low-cost EV chargers across Spain without mistaking a directory lead for live availability.

Volta combines public, user-verifiable charging sources and shows only what the evidence supports. Each station is labeled by confidence and freshness, so live availability never blurs into stale, directory or community information.

Answer the questions that matter before you turn off the road:
• Can I use this charger now?
• What does charging cost?
• What might parking cost?
• Which connector and power are reported?
• Where can I verify the source?

After you allow location access, Volta requests one location when the app launches to load and rank nearby chargers. It requests location again when you tap the locate control. You can decline permission and search for a city or place instead. Coordinates are sent to the configured Volta service for nearby results. If a live source is unavailable, Volta fails closed to source-backed leads instead of presenting uncertain data as ready to route.

Highlights:
• Focus on free charging and prices up to €0.25/kWh
• Spain-wide source-backed charger intelligence
• Live availability where a public live source supports it
• Separate charging-price and parking evidence
• Freshness, confidence and source provenance
• Connector, access and usage instructions where sources provide them
• Route handoff to Apple Maps
• English, Spanish and Russian
• No Volta account, advertising SDK or third-party analytics SDK

Provider apps, operator pages, onsite signs and posted parking rules remain authoritative. Never use the app while driving.
```

**Keywords**

`electric,charger,free,cheap,route,parking,connector,vehicle,stations,nearby`

## Spanish (Spain)

Native-speaker approval is required before submission.

**Subtitle**

`Datos de carga EV en España`

**Promotional text**

`Encuentra carga gratis o económica en España con fuente, vigencia, aparcamiento y conectores visibles antes de iniciar la ruta.`

**Description**

```text
Encuentra cargadores eléctricos gratis o económicos en España sin confundir una pista de directorio con disponibilidad en directo.

Volta combina fuentes públicas y verificables por el usuario, y muestra solo lo que respalda la evidencia. Cada estación indica su nivel de confianza y vigencia, para que la disponibilidad en directo nunca se mezcle con información caducada, de directorio o de la comunidad.

Responde a lo importante antes de desviarte:
• ¿Puedo usar este cargador ahora?
• ¿Cuánto cuesta la carga?
• ¿Cuánto puede costar el aparcamiento?
• ¿Qué conector y potencia se indican?
• ¿Dónde puedo verificar la fuente?

Después de autorizar el acceso a la ubicación, Volta solicita una ubicación al iniciar la app para cargar y ordenar los cargadores cercanos. Vuelve a solicitarla cuando pulsas el control de ubicación. Puedes denegar el permiso y buscar una ciudad o un lugar. Las coordenadas se envían al servicio Volta configurado para obtener resultados cercanos. Si una fuente en directo no está disponible, Volta cierra de forma segura y muestra pistas respaldadas por fuentes, en vez de presentar datos inciertos como listos para la ruta.

Funciones principales:
• Prioridad para carga gratuita y precios de hasta 0,25 €/kWh
• Información de cargadores respaldada por fuentes en toda España
• Disponibilidad en directo cuando existe una fuente pública compatible
• Evidencia separada para precio de carga y coste de aparcamiento
• Vigencia, confianza y procedencia de cada dato
• Conectores, acceso e instrucciones de uso cuando las fuentes los ofrecen
• Envío de la ruta a Mapas de Apple
• Inglés, español y ruso
• Sin cuenta de Volta, SDK de publicidad ni SDK de analítica de terceros

Las aplicaciones de los proveedores, las páginas de los operadores, la señalización del lugar y las normas de aparcamiento son la referencia final. No uses la app mientras conduces.
```

**Keywords**

`eléctrico,cargador,gratis,barato,ruta,aparcamiento,conector,estaciones`

## Russian

Native-speaker approval is required before submission.

**Subtitle**

`Данные о зарядках в Испании`

**Promotional text**

`Ищите бесплатные и недорогие зарядки в Испании: источник, свежесть данных, парковка и разъёмы видны до построения маршрута.`

**Description**

```text
Находите бесплатные и недорогие зарядные станции по всей Испании, не принимая запись из каталога за доступность в реальном времени.

Volta объединяет открытые источники, которые пользователь может проверить сам, и показывает только то, что подтверждают данные. Для каждой станции видны уровень доверия и свежесть, поэтому сведения в реальном времени не смешиваются с устаревшими данными, каталогами или сообщениями сообщества.

Получите ответы до того, как свернёте с дороги:
• Можно ли воспользоваться станцией сейчас?
• Сколько стоит зарядка?
• Сколько может стоить парковка?
• Какой разъём и мощность указаны?
• Где проверить первоисточник?

После разрешения доступа к геопозиции Volta один раз запрашивает её при запуске приложения, чтобы загрузить и ранжировать ближайшие станции. Геопозиция запрашивается снова, когда вы нажимаете кнопку определения местоположения. Можно отказать в разрешении и искать город или место вручную. Координаты передаются настроенному сервису Volta для поиска поблизости. Если источник данных в реальном времени недоступен, Volta безопасно скрывает неподтверждённую готовность и оставляет только варианты со ссылками на источники.

Основные возможности:
• Бесплатная зарядка и цены до 0,25 €/кВт⋅ч в приоритете
• Данные о зарядках по всей Испании со ссылками на источники
• Доступность в реальном времени только при наличии подходящего открытого источника
• Раздельные сведения о цене зарядки и стоимости парковки
• Свежесть, уровень доверия и происхождение данных
• Разъёмы, доступ и инструкции, когда они есть в источнике
• Передача маршрута в Apple Maps
• Английский, испанский и русский языки
• Без учётной записи Volta, рекламных SDK и сторонних SDK аналитики

Приложения поставщиков, страницы операторов, знаки на месте и правила парковки остаются главным источником информации. Не пользуйтесь приложением за рулём.
```

**Keywords**

`зарядка,бесплатно,дёшево,маршрут,парковка`

## Monetization sentence — choose exactly one before submission

The owner must make the free-versus-paid release decision and ensure the binary,
screenshots, review notes, and description agree.

If Volta Plus is enabled in the submitted build, append the localized sentence:

- EN: `Volta Plus is an auto-renewable subscription. Plans, any eligible introductory offer, renewal terms and localized prices are shown by the App Store before purchase.`
- ES: `Volta Plus es una suscripción con renovación automática. Los planes, cualquier oferta de introducción disponible, las condiciones de renovación y los precios locales se muestran antes de comprar.`
- RU: `Volta Plus — автоматически продлеваемая подписка. Перед покупкой App Store показывает планы, доступное вводное предложение, условия продления и цену для вашего региона.`

If the paywall remains disabled, do not mention a paid entitlement or trial in the
description and do not market a subscription that the submitted binary cannot sell.

## Measured limit snapshot

Measured from this file on 2026-07-13 using Unicode character counts for name/subtitle/
promotion/description and UTF-8 byte counts for keywords:

| Field | EN | ES | RU | Apple limit | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Subtitle characters | 28 | 27 | 27 | 30 | Pass |
| Promotional-text characters | 124 | 127 | 123 | 170 | Pass |
| Description characters | 1,597 | 1,888 | 1,855 | 4,000 | Pass |
| Keyword bytes | 75 | 71 | 78 | 100 | Pass |

Name-candidate character counts are 17 (`Volta Carga Clara`), 18 (`Carga Clara
España`), and 20 (`Ruta de Carga España`), all below 30. Re-run the measurement after
any copy edit; this snapshot is not a substitute for the values App Store Connect shows.

## Final metadata checks

- [ ] Final name available in the existing App Store Connect record.
- [ ] Final App Store name is aligned with localized `CFBundleDisplayName` values and the
      installed home-screen label; rebuild, archive, install, and recapture screenshots
      after any name change.
- [ ] No keyword repeats the final name, subtitle, company name, another app/company,
      or a protected mark used only for discovery.
- [ ] Spanish and Russian approved by native speakers.
- [ ] Every feature and monetization claim matches the exact submitted build.
- [ ] Public URLs resolve without login, redirect loops, placeholders, or test content.
- [ ] Submitted binary embeds the same final Privacy, Terms, and Support destinations,
      and every Settings/paywall link has been tapped in the installed Release build.
- [ ] Description remains plain text; no HTML.

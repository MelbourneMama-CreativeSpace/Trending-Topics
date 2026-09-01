"""HTML email template (PRD 42, 43, 44).

Named `mailer`, not `email`, to avoid any confusion with the standard library module.

Email clients are not browsers. The constraints this template works under:

* **Tables for layout.** Outlook renders through Word, which has no flexbox or grid.
* **Inline styles on every element.** Gmail strips `<style>` blocks in some contexts,
  and `<head>` entirely when forwarding.
* **No JavaScript**, no external assets, no web fonts.
* One `@media` block for phones. Clients that ignore it fall back to the inline styles,
  which are already single-column, so nothing breaks.

Jinja2 autoescaping is on. Every value interpolated here is either model output or
third-party feed text, so escaping is a security boundary, not tidiness.
"""

# Warm editorial palette. Deliberately high contrast for accessibility (PRD 44).
INK = "#1c1a17"
MUTED = "#6c6560"
ACCENT = "#b4472e"
PAPER = "#f4f1ea"
CARD = "#ffffff"
RULE = "#e3ded2"

CATEGORY_ICONS = {
    "world": "🌍",
    "politics": "🏛",
    "business": "📈",
    "finance": "💹",
    "technology": "🤖",
    "science": "🔬",
    "sports": "🏆",
    "entertainment": "🎬",
    "culture": "🎭",
    "india": "🇮🇳",
    "australia": "🇦🇺",
    "internet": "🌐",
    "trends": "📊",
    "filmmaking": "🎥",
    "telugu cinema": "🎞",
    "creator economy": "✨",
    "podcasting": "🎙",
    "niche search": "🔎",
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ subject }}</title>
<style>
  @media only screen and (max-width: 620px) {
    .wrap { width: 100% !important; }
    .pad { padding-left: 18px !important; padding-right: 18px !important; }
    .h1 { font-size: 26px !important; }
    .card-h { font-size: 18px !important; }
  }
</style>
</head>
<body style="margin:0;padding:0;background:{{ paper }};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{{ preheader }}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{{ paper }};">
<tr><td align="center" style="padding:18px 10px;">

<table role="presentation" class="wrap" width="620" cellpadding="0" cellspacing="0" border="0"
       style="width:620px;max-width:620px;">

  <tr><td class="pad" style="padding:4px 24px 16px 24px;font-family:Georgia,'Times New Roman',serif;">
    <div style="font-size:11px;letter-spacing:3px;text-transform:uppercase;color:{{ accent }};
                font-family:Helvetica,Arial,sans-serif;font-weight:bold;">Melbourne Mama</div>
    <div class="h1" style="font-size:27px;line-height:1.15;color:{{ ink }};margin-top:6px;">
      Morning Intelligence</div>
    <div style="font-size:13px;color:{{ muted }};margin-top:8px;
                font-family:Helvetica,Arial,sans-serif;">{{ date_line }}</div>
  </td></tr>

  <tr><td style="padding:0 24px;"><div style="height:2px;background:{{ ink }};"></div></td></tr>

  {# ---------------- GLOBAL PULSE ---------------- #}
  <tr><td class="pad" style="padding:20px 24px 4px 24px;font-family:Helvetica,Arial,sans-serif;">
    <div style="font-size:17px;font-weight:bold;color:{{ ink }};">🌍 Global Pulse</div>
    <div style="font-size:13px;color:{{ muted }};margin-top:5px;line-height:1.5;">
      {{ global_intro }}</div>
  </td></tr>

  {% for topic in global_topics %}
  {{ card(topic, loop.index, false) }}
  {% endfor %}

  {# ---------------- CREATIVE RADAR ---------------- #}
  <tr><td style="padding:10px 24px 0 24px;"><div style="height:1px;background:{{ rule }};"></div></td></tr>
  <tr><td class="pad" style="padding:18px 24px 4px 24px;font-family:Helvetica,Arial,sans-serif;">
    <div style="font-size:17px;font-weight:bold;color:{{ ink }};">🎬 Creative Radar</div>
    <div style="font-size:13px;color:{{ muted }};margin-top:5px;line-height:1.5;">
      {{ niche_intro }}</div>
    {% if niche_shortfall %}
    <div style="font-size:12px;color:{{ accent }};margin-top:10px;padding:9px 12px;
                background:#fbf3f0;border-left:3px solid {{ accent }};line-height:1.5;">
      {{ niche_shortfall }}</div>
    {% endif %}
  </td></tr>

  {% for topic in niche_topics %}
  {{ card(topic, loop.index, true) }}
  {% endfor %}

  {# ---------------- CREATIVE SPARK ---------------- #}
  {% if spark %}
  <tr><td class="pad" style="padding:14px 24px 0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{{ ink }};border-radius:10px;">
      <tr><td style="padding:17px 19px;font-family:Helvetica,Arial,sans-serif;">
        <div style="font-size:11px;letter-spacing:2.5px;text-transform:uppercase;
                    color:#e9b8a8;font-weight:bold;">💡 Creative Spark</div>
        <div style="font-size:15px;line-height:1.6;color:#ffffff;margin-top:12px;">
          {{ spark.idea }}</div>
        <div style="font-size:13px;line-height:1.6;color:#b9b2ab;margin-top:12px;">
          {{ spark.rationale }}</div>
        <div style="margin-top:10px;">
          <span style="display:inline-block;font-size:11px;letter-spacing:1.5px;
                       text-transform:uppercase;color:{{ ink }};background:#e9b8a8;
                       padding:5px 11px;border-radius:3px;font-weight:bold;">
            {{ spark.format }}</span></div>
        <div style="font-size:11px;color:#8d857e;margin-top:14px;font-style:italic;">
          An AI-generated suggestion, not reporting.</div>
      </td></tr>
    </table>
  </td></tr>
  {% endif %}

  {# ---------------- FOOTER ---------------- #}
  <tr><td class="pad" style="padding:18px 24px 8px 24px;font-family:Helvetica,Arial,sans-serif;">
    <div style="height:1px;background:{{ rule }};"></div>
    <div style="font-size:11px;color:{{ muted }};margin-top:14px;line-height:1.7;">
      Sources are linked with every story.<br>
      {{ global_topics|length }} global and {{ niche_topics|length }} creative
      {{ 'trend' if (global_topics|length + niche_topics|length) == 1 else 'trends' }}
      from {{ source_count }} sources.<br>
      Melbourne Mama Morning Intelligence &middot; generated {{ generated_line }}
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>
"""

CARD_MACRO = """\
{% macro card(topic, position, is_niche) %}
<tr><td class="pad" style="padding:9px 24px 0 24px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{{ card_bg }};border:1px solid {{ rule }};border-radius:10px;">
    <tr><td style="padding:15px 17px;font-family:Helvetica,Arial,sans-serif;">

      <div style="font-size:11px;letter-spacing:1.6px;text-transform:uppercase;
                  color:{{ muted }};font-weight:bold;">
        {{ '%02d'|format(position) }}
        {% if topic.category %}&nbsp;&nbsp;{{ icon(topic.category) }} {{ topic.category }}{% endif %}
      </div>

      <div class="card-h" style="font-family:Georgia,'Times New Roman',serif;font-size:18px;
                  line-height:1.32;color:{{ ink }};margin-top:9px;">{{ topic.headline }}</div>

      <div style="margin-top:11px;">
        <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                    color:{{ accent }};font-weight:bold;">What happened</div>
        <div style="font-size:13.5px;line-height:1.55;color:{{ ink }};margin-top:5px;">
          {{ topic.what_happened }}</div>
      </div>

      <div style="margin-top:10px;">
        <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                    color:{{ accent }};font-weight:bold;">Why it is trending</div>
        <div style="font-size:13.5px;line-height:1.55;color:{{ ink }};margin-top:5px;">
          {{ topic.why_trending }}</div>
      </div>

      <div style="margin-top:10px;">
        <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                    color:{{ accent }};font-weight:bold;">Why it matters</div>
        <div style="font-size:13.5px;line-height:1.55;color:{{ ink }};margin-top:5px;">
          {{ topic.why_it_matters }}</div>
      </div>

      {% if is_niche and topic.creative_angle %}
      <div style="margin-top:11px;padding:10px 12px;background:{{ paper }};border-radius:7px;">
        <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                    color:{{ ink }};font-weight:bold;">✨ Creative angle</div>
        <div style="font-size:13.5px;line-height:1.55;color:{{ ink }};margin-top:5px;">
          {{ topic.creative_angle }}</div>
        <div style="font-size:11px;color:{{ muted }};margin-top:7px;font-style:italic;">
          An AI-generated suggestion, not reporting.</div>
      </div>
      {% endif %}

      {% if topic.uncertainties %}
      <div style="margin-top:10px;font-size:11.5px;line-height:1.5;color:{{ muted }};">
        <span style="font-weight:bold;">
          {% if topic.conflict_detected %}Sources disagree{% else %}Still unclear{% endif %}:</span>
        {{ topic.uncertainties[:2]|join('; ') }}
      </div>
      {% endif %}

      <div style="margin-top:12px;padding-top:10px;border-top:1px solid {{ rule }};">
        <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                    color:{{ muted }};font-weight:bold;">Read sources</div>
        <div style="margin-top:7px;font-size:13px;line-height:1.85;">
          {% for source in topic.sources %}
          <a href="{{ source.url }}" style="color:{{ accent }};text-decoration:none;">
            {{ source.publisher }}</a>{% if not loop.last %}<span
            style="color:{{ rule }};">&nbsp;&middot;&nbsp;</span>{% endif %}
          {% endfor %}
        </div>
      </div>

    </td></tr>
  </table>
</td></tr>
{% endmacro %}
"""

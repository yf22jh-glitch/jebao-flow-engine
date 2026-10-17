"""Offline browser checks; mocked HA transport never sends a device command.

Run with Python + playwright installed; pass an output directory for screenshots.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    output = Path(sys.argv[1])
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM"))
        page = await browser.new_page(viewport={"width": 1200, "height": 600})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.route(
            "**/*",
            lambda route: route.fulfill(
                body="<html><body></body></html>", content_type="text/html"
            ),
        )
        await page.goto("http://localhost/")
        await page.add_script_tag(
            path=str(Path(__file__).parents[2] / "ha_cards/aqua-schedule-editor.js")
        )
        await page.evaluate("""() => {
          document.body.style='margin:24px;background:#071b29;font-family:Arial,sans-serif';
          window.calls=[];
          window.control=document.createElement('aqua-maintenance-control');
          document.body.append(control);
          window.setState=(phase, changes={})=>{
            const now=new Date().toISOString();
            const maintenance={available:true,enabled:phase!=='off',phase,
              generation:'b'.repeat(64),verified_at:now,
              observed:{master_power:false,slave_power:false,bar_power:false,bar_timer:false},
              ...changes};
            const data={sampled_at:now,maintenance,controller_status:'active',
              active_sha256:'a'.repeat(64),upper_role:'slave',lower_role:'master',
              command_topic:'test/request',busy:false};
            window.hass={states:{'sensor.aquarium_gmp_schedule_editor':
              {state:'active',attributes:data}},callService:async (...args)=>calls.push(args)};
            control.hass=hass;
          };
        }""")
        start = page.get_by_role("button", name="환수 시작 · 세 모터 끄기", exact=True)
        finish = page.get_by_role("button", name="환수 종료 · 전원·시간표 복귀", exact=True)
        status = page.get_by_role("status")
        await page.evaluate("setState('off')")
        assert await start.is_enabled() and await finish.is_disabled()
        assert await page.evaluate("calls.length") == 0
        await page.evaluate("setState('powered_off')")
        assert "세 모터 전원 OFF 확인" in await status.inner_text()
        assert await start.is_disabled() and await finish.is_enabled()
        await page.screenshot(path=str(output / "maintenance-desktop.png"))
        await page.set_viewport_size({"width": 390, "height": 650})
        assert await page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        await page.screenshot(path=str(output / "maintenance-mobile.png"))
        await page.evaluate(
            "setState('powered_off',{observed:{master_power:false,slave_power:false,"
            "bar_power:true,bar_timer:false}})"
        )
        assert "OFF 확인" not in await status.inner_text()
        await page.evaluate("setState('powered_off',{verified_at:'2026-01-01T00:00:00Z'})")
        assert "OFF 확인" not in await status.inner_text()
        await page.evaluate("setState('feeding')")
        assert "이전 Feeding" in await status.inner_text()
        await page.evaluate("setState('error')")
        assert await page.get_by_role("button", name="전원 OFF 다시 확인").is_enabled()
        assert await finish.is_enabled()
        await page.evaluate("setState('off')")
        await start.focus()
        await page.evaluate(
            "window.pressed=control.shadowRoot.querySelector('button');setState('off')"
        )
        assert await page.evaluate("pressed===control.shadowRoot.activeElement")
        position = await start.bounding_box()
        await page.mouse.move(position["x"] + 20, position["y"] + 20)
        await page.mouse.down()
        await page.evaluate("setState('off')")
        await page.mouse.up()
        await page.evaluate("control._send('maintenance_on')")
        assert await page.evaluate("calls.length") == 1
        args = await page.evaluate("calls[0]")
        payload = json.loads(args[2]["payload"])
        assert args[:2] == ["mqtt", "publish"]
        assert payload["operation"] == "maintenance_on"
        assert args[2]["qos"] == 0 and args[2]["retain"] is False
        await page.evaluate(
            """id=>{
          setState('powered_off');
          hass.states['sensor.aquarium_gmp_schedule_editor'].attributes.last_result={request_id:id};
          control.hass=hass;
        }""",
            payload["request_id"],
        )
        assert await finish.is_enabled()
        await finish.click()
        assert await page.evaluate("calls.length") == 2
        assert (
            json.loads((await page.evaluate("calls[1]"))[2]["payload"])["operation"]
            == "maintenance_off"
        )
        await page.add_script_tag(
            path=str(Path(__file__).parents[2] / "ha_cards/aqua-overview-card.js")
        )
        assert await page.evaluate("""() => {
          const outer=document.createElement('aqua-overview-card');
          outer._patchMarkup('<section><p>before</p><aqua-maintenance-control></aqua-maintenance-control></section>');
          const child=outer.shadowRoot.querySelector('aqua-maintenance-control');
          outer._patchMarkup('<section><p>after</p><aqua-maintenance-control></aqua-maintenance-control></section>');
          return child===outer.shadowRoot.querySelector('aqua-maintenance-control');
        }""")
        assert errors == [], errors
        await browser.close()
    print(
        "Browser checks passed: OFF verification, stale/mixed/legacy states, "
        "explicit resume, duplicate clicks, mobile layout"
    )


asyncio.run(main())

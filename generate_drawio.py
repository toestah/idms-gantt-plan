#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate draw.io swimlane timeline diagrams from project management JSON data.

Usage:
    python generate_drawio.py project.json -o output.drawio
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any
import html


# Status color mapping
STATUS_COLORS = {
    "on_track": "#4CAF50",
    "complete": "#4CAF50",
    "in_progress": "#2196F3",
    "at_risk": "#FFC107",
    "delayed": "#F44336",
    "blocked": "#F44336",
    "not_started": "#9E9E9E",
}

# Risk severity colors
SEVERITY_COLORS = {
    "high": "#F44336",
    "medium": "#FFC107",
    "low": "#4CAF50",
}

# Layout constants
SWIMLANE_HEADER_WIDTH = 150
SWIMLANE_MIN_HEIGHT = 60
TIMELINE_HEADER_HEIGHT = 70
PIXELS_PER_DAY = 30
TASK_HEIGHT = 32
TASK_MIN_HEIGHT = 28
TASK_MAX_HEIGHT = 38
TASK_DEFAULT_FONT_SIZE = 11
TASK_MIN_FONT_SIZE = 8
CHARS_PER_PIXEL = 0.14  # Approximate characters per pixel at font size 11
TASK_VERTICAL_PADDING = 3  # Small gap between stacked lanes
SWIMLANE_VERTICAL_PADDING = 4  # Small padding at top/bottom of swimlane
MILESTONE_SIZE = 20
MILESTONE_VERTICAL_SPACING = 45  # Space needed per milestone (diamond + label)
RISK_MARKER_SIZE = 16
TABLE_ROW_HEIGHT = 45
TABLE_START_Y_OFFSET = 80


def parse_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d")

def calculate_task_dimensions(name: str, owner: str, width: float) -> tuple[float, int]:
    """Calculate task box height and font size based on text length and available width.
    
    Returns (height, font_size)
    """
    # Calculate total text length (name is more important, owner is smaller)
    text_length = len(name)
    
    # Estimate how many characters can fit on one line at default font size
    # Subtract padding (about 10px on each side)
    usable_width = max(width - 20, 30)
    chars_per_line = usable_width * CHARS_PER_PIXEL
    
    # If text fits on ~1.5 lines at default font, use defaults
    if text_length <= chars_per_line * 1.5:
        return (TASK_HEIGHT, TASK_DEFAULT_FONT_SIZE)
    
    # Try reducing font size first
    for font_size in range(TASK_DEFAULT_FONT_SIZE - 1, TASK_MIN_FONT_SIZE - 1, -1):
        # Font size reduction increases chars per pixel roughly linearly
        scale = TASK_DEFAULT_FONT_SIZE / font_size
        adjusted_chars = chars_per_line * scale
        if text_length <= adjusted_chars * 1.5:
            return (TASK_HEIGHT, font_size)
    
    # Text is still too long - calculate needed height for wrapping
    # At minimum font size
    scale = TASK_DEFAULT_FONT_SIZE / TASK_MIN_FONT_SIZE
    chars_at_min = chars_per_line * scale
    lines_needed = max(1, text_length / max(chars_at_min, 5))
    
    # Each line needs about 14px at small font, plus some padding
    line_height = 13
    needed_height = lines_needed * line_height + 8  # 8px padding
    
    # Clamp to max height
    final_height = min(max(needed_height, TASK_MIN_HEIGHT), TASK_MAX_HEIGHT)
    
    return (final_height, TASK_MIN_FONT_SIZE)




def date_to_x(date: datetime, project_start: datetime) -> float:
    """Convert a date to X coordinate."""
    days = (date - project_start).days
    return SWIMLANE_HEADER_WIDTH + (days * PIXELS_PER_DAY)


def escape_xml(text: str) -> str:
    """Escape text for XML attributes."""
    return html.escape(str(text), quote=True)


_id_counter = 0

def generate_id() -> str:
    """Generate a unique ID for mxCell elements."""
    global _id_counter
    _id_counter += 1
    return f"cell_{_id_counter}"


class DrawioGenerator:
    """Generate draw.io XML from project data."""

    def __init__(self, data: dict):
        self.data = data
        self.project = data["project"]
        self.workstreams = {ws["id"]: ws for ws in data.get("workstreams", [])}
        self.tasks = {t["id"]: t for t in data.get("tasks", [])}
        self.milestones = {m["id"]: m for m in data.get("milestones", [])}
        self.dependencies = data.get("dependencies", [])
        self.risks = data.get("risks", [])
        self.date_markers = data.get("date_markers", [])

        # Parse project dates
        self.project_start = parse_date(self.project["start_date"])
        self.project_end = parse_date(self.project["end_date"])

        # Calculate visual end date (extend to Saturday of the week containing project_end)
        # Python weekday: Monday=0, Tuesday=1, ..., Saturday=5, Sunday=6
        days_until_saturday = (5 - self.project_end.weekday()) % 7
        if days_until_saturday == 0 and self.project_end.weekday() != 5:
            # If it's Sunday, go to next Saturday
            days_until_saturday = 6
        self.visual_end = self.project_end + timedelta(days=days_until_saturday)

        # Build risk lookup by affected item
        self.risks_by_item = {}
        for risk in self.risks:
            for item_id in risk.get("affected", []):
                if item_id not in self.risks_by_item:
                    self.risks_by_item[item_id] = []
                self.risks_by_item[item_id].append(risk)

        # Track cell IDs for dependency connections
        self.cell_ids = {}
        self.cell_positions = {}

        # Calculate layout with overlap handling
        self.task_rows = {}  # task_id -> row number within workstream
        self.swimlane_rows = {}  # ws_id -> number of rows
        self.task_heights = {}  # task_id -> calculated height
        self.task_font_sizes = {}  # task_id -> calculated font size
        self.global_row_height = TASK_HEIGHT  # Global consistent row height across all workstreams
        self.swimlane_heights = {}  # ws_id -> computed height
        self.swimlane_y_starts = {}  # ws_id -> Y coordinate where swimlane starts
        self._calculate_layout()

        # Calculate milestone layout (group by date, determine vertical stacking)
        self.milestones_by_date = {}
        self.max_milestone_stack = 0
        self._calculate_milestone_layout()

    def _tasks_overlap(self, task1: dict, task2: dict) -> bool:
        """Check if two tasks have overlapping date ranges (inclusive of end dates)."""
        start1 = parse_date(task1["start"])
        end1 = parse_date(task1["end"])
        start2 = parse_date(task2["start"])
        end2 = parse_date(task2["end"])
        # Tasks overlap if their date ranges intersect (inclusive)
        # Two ranges [a,b] and [c,d] intersect if a <= d AND c <= b
        return start1 <= end2 and start2 <= end1

    def _calculate_layout(self):
        """Calculate task row assignments and swimlane heights to handle overlaps."""
        ws_list = list(self.workstreams.keys())

        # Group tasks by workstream
        tasks_by_ws = {ws_id: [] for ws_id in ws_list}
        for task_id, task in self.tasks.items():
            ws_id = task.get("workstream")
            if ws_id in tasks_by_ws:
                tasks_by_ws[ws_id].append((task_id, task))

        # For each workstream, assign tasks to rows
        for ws_id in ws_list:
            ws_tasks = tasks_by_ws[ws_id]
            # Sort tasks by start date
            ws_tasks.sort(key=lambda t: parse_date(t[1]["start"]))

            # Rows are lists of tasks assigned to that row
            rows = []

            for task_id, task in ws_tasks:
                # Calculate task width and dimensions
                start_date = parse_date(task["start"])
                end_date = parse_date(task["end"])
                # +1 to include end date fully
                task_days = (end_date - start_date).days + 1
                task_width = task_days * PIXELS_PER_DAY
                
                # Calculate optimal height and font size
                height, font_size = calculate_task_dimensions(
                    task.get("name", ""),
                    task.get("owner", ""),
                    task_width
                )
                self.task_heights[task_id] = height
                self.task_font_sizes[task_id] = font_size
                
                # Find the first row where this task doesn't overlap with existing tasks
                assigned = False
                for row_idx, row_tasks in enumerate(rows):
                    overlaps = False
                    for existing_task in row_tasks:
                        if self._tasks_overlap(task, existing_task):
                            overlaps = True
                            break
                    if not overlaps:
                        rows[row_idx].append(task)
                        self.task_rows[task_id] = row_idx
                        assigned = True
                        break

                if not assigned:
                    # Create a new row
                    rows.append([task])
                    self.task_rows[task_id] = len(rows) - 1

            # Store number of rows for this workstream
            self.swimlane_rows[ws_id] = max(len(rows), 1)

        # Calculate global row height (max task height across ALL workstreams)
        self.global_row_height = TASK_HEIGHT
        for task_id in self.task_heights:
            self.global_row_height = max(self.global_row_height, self.task_heights[task_id])
        
        # Now calculate swimlane heights using the global row height
        # Height = content height + minimal padding, centered
        for ws_id in ws_list:
            num_rows = self.swimlane_rows.get(ws_id, 1)
            content_height = num_rows * self.global_row_height + (num_rows - 1) * TASK_VERTICAL_PADDING
            height = content_height + 2 * SWIMLANE_VERTICAL_PADDING
            self.swimlane_heights[ws_id] = max(height, SWIMLANE_MIN_HEIGHT)

        # Calculate Y start positions for each swimlane
        # Note: milestone row offset is added in get_workstream_y since milestone layout
        # is calculated after this method
        current_y = TIMELINE_HEADER_HEIGHT
        for ws_id in ws_list:
            self.swimlane_y_starts[ws_id] = current_y
            current_y += self.swimlane_heights[ws_id]

    def _calculate_milestone_layout(self):
        """Group milestones by date and calculate vertical stacking needs."""
        for ms_id, milestone in self.milestones.items():
            date_str = milestone["date"]
            if date_str not in self.milestones_by_date:
                self.milestones_by_date[date_str] = []
            self.milestones_by_date[date_str].append((ms_id, milestone))

        # Find the maximum number of milestones on any single date
        if self.milestones_by_date:
            self.max_milestone_stack = max(len(ms_list) for ms_list in self.milestones_by_date.values())
        else:
            self.max_milestone_stack = 0

    def get_milestone_area_height(self) -> float:
        """Calculate the height needed for the milestone area."""
        if self.max_milestone_stack == 0:
            return 0
        return self.max_milestone_stack * MILESTONE_VERTICAL_SPACING + 10  # +10 for padding

    def get_workstream_y(self, ws_id: str) -> float:
        """Get Y coordinate for a workstream (accounts for milestone row at top)."""
        base_y = self.swimlane_y_starts.get(ws_id, TIMELINE_HEADER_HEIGHT)
        # Add milestone area height since it's at the top
        return base_y + self.get_milestone_area_height()

    def get_swimlane_height(self, ws_id: str) -> float:
        """Get the computed height for a workstream's swimlane."""
        return self.swimlane_heights.get(ws_id, SWIMLANE_MIN_HEIGHT)

    def get_task_y(self, ws_id: str, task_id: str) -> float:
        """Get Y coordinate for a task within a workstream, accounting for row stacking."""
        ws_y = self.get_workstream_y(ws_id)
        row = self.task_rows.get(task_id, 0)
        
        # Calculate content height for this workstream
        num_rows = self.swimlane_rows.get(ws_id, 1)
        content_height = num_rows * self.global_row_height + (num_rows - 1) * TASK_VERTICAL_PADDING
        
        # Calculate offset to center content within swimlane
        swimlane_height = self.get_swimlane_height(ws_id)
        center_offset = (swimlane_height - content_height) / 2
        
        # Position task: swimlane_y + centering_offset + row_position
        return ws_y + center_offset + row * (self.global_row_height + TASK_VERTICAL_PADDING)

    def calculate_diagram_size(self) -> tuple[float, float]:
        """Calculate total diagram dimensions."""
        # Add 1 to include the end date fully (dates are drawn at left edge, so we need +1 for end of day)
        days = (self.visual_end - self.project_start).days + 1
        width = SWIMLANE_HEADER_WIDTH + (days * PIXELS_PER_DAY) + 50

        # Sum up all swimlane heights
        height = TIMELINE_HEADER_HEIGHT
        for ws_id in self.workstreams:
            height += self.get_swimlane_height(ws_id)

        # Add milestone area (dynamically sized based on overlap)
        height += self.get_milestone_area_height()

        # Add space for risk table
        if self.risks:
            height += TABLE_START_Y_OFFSET + TABLE_ROW_HEIGHT * (len(self.risks) + 1) + 50

        return width, height

    def create_mxfile(self) -> ET.Element:
        """Create the root mxfile element."""
        mxfile = ET.Element("mxfile")
        mxfile.set("host", "app.diagrams.net")
        mxfile.set("modified", datetime.now().isoformat())
        mxfile.set("agent", "Project Timeline Generator")
        mxfile.set("version", "21.0.0")
        mxfile.set("type", "device")
        return mxfile

    def create_diagram(self, mxfile: ET.Element) -> ET.Element:
        """Create the diagram element."""
        diagram = ET.SubElement(mxfile, "diagram")
        diagram.set("id", "project-timeline")
        diagram.set("name", escape_xml(self.project.get("name", "Project Timeline")))
        return diagram

    def create_graph_model(self, diagram: ET.Element) -> ET.Element:
        """Create the mxGraphModel element."""
        width, height = self.calculate_diagram_size()

        model = ET.SubElement(diagram, "mxGraphModel")
        model.set("dx", "0")
        model.set("dy", "0")
        model.set("grid", "1")
        model.set("gridSize", "10")
        model.set("guides", "1")
        model.set("tooltips", "1")
        model.set("connect", "1")
        model.set("arrows", "1")
        model.set("fold", "1")
        model.set("page", "1")
        model.set("pageScale", "1")
        model.set("pageWidth", str(int(width)))
        model.set("pageHeight", str(int(height)))
        model.set("math", "0")
        model.set("shadow", "0")

        return model

    def add_root_cells(self, root: ET.Element):
        """Add the required root cells (0 and 1)."""
        cell0 = ET.SubElement(root, "mxCell")
        cell0.set("id", "0")

        cell1 = ET.SubElement(root, "mxCell")
        cell1.set("id", "1")
        cell1.set("parent", "0")

    def add_weekend_shading(self, root: ET.Element):
        """Add grey shading for weekend days (Saturday and Sunday)."""
        show_weekends = self.project.get("show_weekends", True)
        if not show_weekends:
            return

        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        # Include milestone area in shading
        total_swimlane_height += self.get_milestone_area_height()

        # Iterate through each day in the visual range (extends to end of week)
        current = self.project_start
        while current <= self.visual_end:
            # Check if it's Saturday (5) or Sunday (6)
            if current.weekday() in (5, 6):
                x = date_to_x(current, self.project_start)

                # Weekend shading rectangle
                weekend_cell = ET.SubElement(root, "mxCell")
                weekend_cell.set("id", generate_id())
                weekend_cell.set("value", "")
                weekend_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#E8E8E8;strokeColor=none;opacity=50;")
                weekend_cell.set("vertex", "1")
                weekend_cell.set("parent", "1")

                geom = ET.SubElement(weekend_cell, "mxGeometry")
                geom.set("x", str(int(x)))
                geom.set("y", str(TIMELINE_HEADER_HEIGHT))
                geom.set("width", str(PIXELS_PER_DAY))
                geom.set("height", str(int(total_swimlane_height)))
                geom.set("as", "geometry")

            current += timedelta(days=1)

    def add_timeline_header(self, root: ET.Element):
        """Add timeline header with week markers aligned to Sundays (Sun-Sat weeks)."""
        width, _ = self.calculate_diagram_size()
        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        # Include milestone area in gridline span
        total_swimlane_height += self.get_milestone_area_height()

        # Header background
        header_cell = ET.SubElement(root, "mxCell")
        header_id = generate_id()
        header_cell.set("id", header_id)
        header_cell.set("value", "")
        header_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;")
        header_cell.set("vertex", "1")
        header_cell.set("parent", "1")

        geom = ET.SubElement(header_cell, "mxGeometry")
        geom.set("x", str(SWIMLANE_HEADER_WIDTH))
        geom.set("y", "0")
        geom.set("width", str(int(width - SWIMLANE_HEADER_WIDTH)))
        geom.set("height", str(TIMELINE_HEADER_HEIGHT))
        geom.set("as", "geometry")

        # Find the first Sunday on or before project start
        current = self.project_start
        # Python weekday: Monday=0, Sunday=6
        # We want to find the Sunday before or on current date
        days_since_sunday = (current.weekday() + 1) % 7
        first_sunday = current - timedelta(days=days_since_sunday)

        # Add week markers aligned to Sundays
        week_num = 1
        current = first_sunday

        while current <= self.visual_end:
            x = date_to_x(current, self.project_start)

            # Sunday vertical line (solid, darker) - marks start of week
            if x >= SWIMLANE_HEADER_WIDTH - PIXELS_PER_DAY:
                line_cell = ET.SubElement(root, "mxCell")
                line_cell.set("id", generate_id())
                line_cell.set("value", "")
                line_cell.set("style", "endArrow=none;html=1;strokeColor=#999999;strokeWidth=1;")
                line_cell.set("edge", "1")
                line_cell.set("parent", "1")

                line_geom = ET.SubElement(line_cell, "mxGeometry")
                line_geom.set("relative", "1")
                line_geom.set("as", "geometry")

                source = ET.SubElement(line_geom, "mxPoint")
                source.set("x", str(int(x)))
                source.set("y", str(TIMELINE_HEADER_HEIGHT))
                source.set("as", "sourcePoint")

                target = ET.SubElement(line_geom, "mxPoint")
                target.set("x", str(int(x)))
                target.set("y", str(int(TIMELINE_HEADER_HEIGHT + total_swimlane_height)))
                target.set("as", "targetPoint")

            # Only draw labels if within visible area
            if x >= SWIMLANE_HEADER_WIDTH:
                # Week label with date range
                week_end = current + timedelta(days=6)
                label = f"W{week_num}: {current.strftime('%b %d')}"

                label_cell = ET.SubElement(root, "mxCell")
                label_cell.set("id", generate_id())
                label_cell.set("value", escape_xml(label))
                label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontStyle=1;")
                label_cell.set("vertex", "1")
                label_cell.set("parent", "1")

                label_geom = ET.SubElement(label_cell, "mxGeometry")
                label_geom.set("x", str(int(x + 2)))
                label_geom.set("y", "3")
                label_geom.set("width", str(int(PIXELS_PER_DAY * 7)))
                label_geom.set("height", "15")
                label_geom.set("as", "geometry")

            # Add day labels (S M T W T F S) and day numbers for all 7 days
            day_labels = ["S", "M", "T", "W", "T", "F", "S"]
            for day_idx, day_label in enumerate(day_labels):
                day_date = current + timedelta(days=day_idx)
                day_x = date_to_x(day_date, self.project_start)

                # Only draw if within visible area
                if day_x < SWIMLANE_HEADER_WIDTH:
                    continue

                # Day letter
                day_cell = ET.SubElement(root, "mxCell")
                day_cell.set("id", generate_id())
                day_cell.set("value", day_label)
                # Weekend days in grey
                if day_idx in (0, 6):  # Sunday or Saturday
                    day_style = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontColor=#999999;fontStyle=1;"
                else:
                    day_style = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontColor=#333333;fontStyle=1;"
                day_cell.set("style", day_style)
                day_cell.set("vertex", "1")
                day_cell.set("parent", "1")

                day_geom = ET.SubElement(day_cell, "mxGeometry")
                day_geom.set("x", str(int(day_x)))
                day_geom.set("y", "22")
                day_geom.set("width", str(PIXELS_PER_DAY))
                day_geom.set("height", "14")
                day_geom.set("as", "geometry")

                # Day number
                day_num = day_date.day
                num_cell = ET.SubElement(root, "mxCell")
                num_cell.set("id", generate_id())
                num_cell.set("value", str(day_num))
                # Weekend days in grey
                if day_idx in (0, 6):  # Sunday or Saturday
                    num_style = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontColor=#999999;"
                else:
                    num_style = "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontColor=#666666;"
                num_cell.set("style", num_style)
                num_cell.set("vertex", "1")
                num_cell.set("parent", "1")

                num_geom = ET.SubElement(num_cell, "mxGeometry")
                num_geom.set("x", str(int(day_x)))
                num_geom.set("y", "38")
                num_geom.set("width", str(PIXELS_PER_DAY))
                num_geom.set("height", "14")
                num_geom.set("as", "geometry")

                # Light vertical gridline for each day (except Sunday which has week line)
                if day_idx > 0:
                    day_line = ET.SubElement(root, "mxCell")
                    day_line.set("id", generate_id())
                    day_line.set("value", "")
                    day_line.set("style", "endArrow=none;html=1;strokeColor=#E0E0E0;strokeWidth=1;")
                    day_line.set("edge", "1")
                    day_line.set("parent", "1")

                    day_line_geom = ET.SubElement(day_line, "mxGeometry")
                    day_line_geom.set("relative", "1")
                    day_line_geom.set("as", "geometry")

                    day_source = ET.SubElement(day_line_geom, "mxPoint")
                    day_source.set("x", str(int(day_x)))
                    day_source.set("y", str(TIMELINE_HEADER_HEIGHT))
                    day_source.set("as", "sourcePoint")

                    day_target = ET.SubElement(day_line_geom, "mxPoint")
                    day_target.set("x", str(int(day_x)))
                    day_target.set("y", str(int(TIMELINE_HEADER_HEIGHT + total_swimlane_height)))
                    day_target.set("as", "targetPoint")

            week_num += 1
            current += timedelta(weeks=1)

    def add_swimlanes(self, root: ET.Element):
        """Add swimlane containers for each workstream."""
        width, _ = self.calculate_diagram_size()

        for ws_id in self.workstreams:
            ws = self.workstreams[ws_id]
            y = self.get_workstream_y(ws_id)
            height = self.get_swimlane_height(ws_id)
            color = ws.get("color", "#E3F2FD")

            # Swimlane header
            header_cell = ET.SubElement(root, "mxCell")
            header_id = f"swimlane_{ws_id}"
            header_cell.set("id", header_id)
            header_cell.set("value", escape_xml(ws["name"]))
            header_cell.set("style", f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#666666;fontStyle=1;fontSize=12;verticalAlign=middle;")
            header_cell.set("vertex", "1")
            header_cell.set("parent", "1")

            geom = ET.SubElement(header_cell, "mxGeometry")
            geom.set("x", "0")
            geom.set("y", str(int(y)))
            geom.set("width", str(SWIMLANE_HEADER_WIDTH))
            geom.set("height", str(int(height)))
            geom.set("as", "geometry")

            # Swimlane row background
            row_cell = ET.SubElement(root, "mxCell")
            row_cell.set("id", generate_id())
            row_cell.set("value", "")
            # Lighter version of the color
            row_cell.set("style", f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};fillOpacity=20;strokeColor=#CCCCCC;")
            row_cell.set("vertex", "1")
            row_cell.set("parent", "1")

            row_geom = ET.SubElement(row_cell, "mxGeometry")
            row_geom.set("x", str(SWIMLANE_HEADER_WIDTH))
            row_geom.set("y", str(int(y)))
            row_geom.set("width", str(int(width - SWIMLANE_HEADER_WIDTH)))
            row_geom.set("height", str(int(height)))
            row_geom.set("as", "geometry")

    def add_milestone_row(self, root: ET.Element):
        """Add a dedicated row for milestones at the top (below timeline header)."""
        milestone_height = self.get_milestone_area_height()
        if milestone_height == 0:
            return

        width, _ = self.calculate_diagram_size()
        # Milestone row is at the top, right after the timeline header
        row_y = TIMELINE_HEADER_HEIGHT

        # Milestone row header
        header_cell = ET.SubElement(root, "mxCell")
        header_cell.set("id", generate_id())
        header_cell.set("value", "Milestones")
        header_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#E1BEE7;strokeColor=#666666;fontStyle=1;fontSize=12;verticalAlign=middle;")
        header_cell.set("vertex", "1")
        header_cell.set("parent", "1")

        geom = ET.SubElement(header_cell, "mxGeometry")
        geom.set("x", "0")
        geom.set("y", str(int(row_y)))
        geom.set("width", str(SWIMLANE_HEADER_WIDTH))
        geom.set("height", str(int(milestone_height)))
        geom.set("as", "geometry")

        # Milestone row background
        row_cell = ET.SubElement(root, "mxCell")
        row_cell.set("id", generate_id())
        row_cell.set("value", "")
        row_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#E1BEE7;fillOpacity=20;strokeColor=#CCCCCC;")
        row_cell.set("vertex", "1")
        row_cell.set("parent", "1")

        row_geom = ET.SubElement(row_cell, "mxGeometry")
        row_geom.set("x", str(SWIMLANE_HEADER_WIDTH))
        row_geom.set("y", str(int(row_y)))
        row_geom.set("width", str(int(width - SWIMLANE_HEADER_WIDTH)))
        row_geom.set("height", str(int(milestone_height)))
        row_geom.set("as", "geometry")

    def add_date_markers(self, root: ET.Element):
        """Add vertical date marker lines for important dates (lines only, labels added separately)."""
        if not self.date_markers:
            return

        # Calculate the vertical span for markers
        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        # Include milestone area in marker span
        total_swimlane_height += self.get_milestone_area_height()
        marker_top = TIMELINE_HEADER_HEIGHT
        marker_bottom = TIMELINE_HEADER_HEIGHT + total_swimlane_height

        for marker in self.date_markers:
            marker_date = parse_date(marker["date"])

            # Skip markers outside project range
            if marker_date < self.project_start or marker_date > self.project_end:
                continue

            x = date_to_x(marker_date, self.project_start)
            # Position "end" means end of the day (right edge), "start" means start of day (left edge)
            if marker.get("position", "start") == "end":
                x += PIXELS_PER_DAY
            color = marker.get("color", "#FF0000")
            style = marker.get("style", "dashed")

            # Determine stroke style
            if style == "dashed":
                stroke_style = "dashed=1;dashPattern=8 4;"
            elif style == "dotted":
                stroke_style = "dashed=1;dashPattern=2 2;"
            else:
                stroke_style = ""

            # Vertical marker line
            line_cell = ET.SubElement(root, "mxCell")
            line_cell.set("id", generate_id())
            line_cell.set("value", "")
            line_cell.set("style", f"endArrow=none;html=1;strokeColor={color};strokeWidth=2;{stroke_style}")
            line_cell.set("edge", "1")
            line_cell.set("parent", "1")

            line_geom = ET.SubElement(line_cell, "mxGeometry")
            line_geom.set("relative", "1")
            line_geom.set("as", "geometry")

            source = ET.SubElement(line_geom, "mxPoint")
            source.set("x", str(int(x)))
            source.set("y", str(int(marker_top)))
            source.set("as", "sourcePoint")

            target = ET.SubElement(line_geom, "mxPoint")
            target.set("x", str(int(x)))
            target.set("y", str(int(marker_bottom)))
            target.set("as", "targetPoint")

    def add_date_marker_labels(self, root: ET.Element):
        """Add date marker labels in the milestone area at top (called last to ensure they appear on top)."""
        if not self.date_markers:
            return

        # Milestone row is at the top, right after timeline header
        milestone_row_y = TIMELINE_HEADER_HEIGHT

        for marker in self.date_markers:
            marker_date = parse_date(marker["date"])

            # Skip markers outside project range
            if marker_date < self.project_start or marker_date > self.project_end:
                continue

            x = date_to_x(marker_date, self.project_start)
            # Position "end" means end of the day (right edge), "start" means start of day (left edge)
            if marker.get("position", "start") == "end":
                x += PIXELS_PER_DAY
            color = marker.get("color", "#FF0000")
            name = marker.get("name", "")

            # Marker label in milestone area, offset slightly to the right of the line
            if name:
                # Position in milestone area, just right of the line
                label_x = x + 4
                label_y = milestone_row_y + 5

                # Background for better readability
                bg_cell = ET.SubElement(root, "mxCell")
                bg_cell.set("id", generate_id())
                bg_cell.set("value", "")
                bg_cell.set("style", "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=none;opacity=85;")
                bg_cell.set("vertex", "1")
                bg_cell.set("parent", "1")

                bg_geom = ET.SubElement(bg_cell, "mxGeometry")
                bg_geom.set("x", str(int(label_x - 2)))
                bg_geom.set("y", str(int(label_y - 1)))
                bg_geom.set("width", str(len(name) * 7 + 8))
                bg_geom.set("height", str(16))
                bg_geom.set("as", "geometry")

                # Label text
                label_cell = ET.SubElement(root, "mxCell")
                label_cell.set("id", generate_id())
                label_cell.set("value", f"<b>{escape_xml(name)}</b>")
                label_cell.set("style", f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontColor={color};")
                label_cell.set("vertex", "1")
                label_cell.set("parent", "1")

                label_geom = ET.SubElement(label_cell, "mxGeometry")
                label_geom.set("x", str(int(label_x)))
                label_geom.set("y", str(int(label_y)))
                label_geom.set("width", str(len(name) * 7 + 4))
                label_geom.set("height", str(14))
                label_geom.set("as", "geometry")

    def add_tasks(self, root: ET.Element):
        """Add task bars to the diagram."""
        for task_id, task in self.tasks.items():
            ws_id = task["workstream"]
            if ws_id not in self.workstreams:
                continue

            start_date = parse_date(task["start"])
            end_date = parse_date(task["end"])

            x = date_to_x(start_date, self.project_start)
            y = self.get_task_y(ws_id, task_id)
            # Add 1 to include the end date (tasks run through end of the end date)
            width = ((end_date - start_date).days + 1) * PIXELS_PER_DAY

            status = task.get("status", "not_started")
            status_color = STATUS_COLORS.get(status, STATUS_COLORS["not_started"])
            percent = task.get("percent_complete", 0)

            # Get calculated dimensions for this task
            task_height = self.task_heights.get(task_id, TASK_HEIGHT)
            task_font_size = self.task_font_sizes.get(task_id, TASK_DEFAULT_FONT_SIZE)

            # Store position for dependencies
            cell_id = f"task_{task_id}"
            self.cell_ids[task_id] = cell_id
            self.cell_positions[task_id] = {
                "x": x, "y": y, "width": width, "height": task_height,
                "center_x": x + width / 2, "center_y": y + task_height / 2,
                "right_x": x + width, "left_x": x
            }

            # Task container (background)
            task_cell = ET.SubElement(root, "mxCell")
            task_cell.set("id", cell_id)

            # Build label with name and owner
            owner = task.get("owner", "")
            label = f"<b>{escape_xml(task['name'])}</b>"
            if owner:
                owner_font_size = max(task_font_size - 1, 7)
                label += f"<br><font style=\"font-size:{owner_font_size}px\">{escape_xml(owner)}</font>"

            task_cell.set("value", label)
            task_cell.set("style", f"rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor={status_color};strokeWidth=2;verticalAlign=middle;fontSize={task_font_size};")
            task_cell.set("vertex", "1")
            task_cell.set("parent", "1")

            geom = ET.SubElement(task_cell, "mxGeometry")
            geom.set("x", str(int(x)))
            geom.set("y", str(int(y)))
            geom.set("width", str(int(max(width, 40))))
            geom.set("height", str(int(task_height)))
            geom.set("as", "geometry")

            # Progress fill overlay
            if percent > 0:
                progress_width = (width * percent / 100)
                progress_cell = ET.SubElement(root, "mxCell")
                progress_cell.set("id", generate_id())
                progress_cell.set("value", "")
                progress_cell.set("style", f"rounded=1;whiteSpace=wrap;html=1;fillColor={status_color};fillOpacity=30;strokeColor=none;")
                progress_cell.set("vertex", "1")
                progress_cell.set("parent", "1")

                prog_geom = ET.SubElement(progress_cell, "mxGeometry")
                prog_geom.set("x", str(int(x)))
                prog_geom.set("y", str(int(y)))
                prog_geom.set("width", str(int(max(progress_width, 5))))
                prog_geom.set("height", str(int(task_height)))
                prog_geom.set("as", "geometry")

            # Add risk markers if this task has associated risks
            self.add_risk_markers(root, task_id, x + width - 15, y - 5)

    def add_milestones(self, root: ET.Element):
        """Add milestone diamonds to the dedicated milestone row at top."""
        if not self.milestones:
            return

        # Milestone row is at the top, right after timeline header
        milestone_row_y = TIMELINE_HEADER_HEIGHT

        for date_str, ms_list in self.milestones_by_date.items():
            ms_date = parse_date(date_str)
            x = date_to_x(ms_date, self.project_start) - MILESTONE_SIZE / 2

            for idx, (ms_id, milestone) in enumerate(ms_list):
                # Offset vertically for multiple milestones on same date
                y = milestone_row_y + 5 + (idx * MILESTONE_VERTICAL_SPACING)

                status = milestone.get("status", "on_track")
                status_color = STATUS_COLORS.get(status, STATUS_COLORS["on_track"])

                # Store position for dependencies
                cell_id = f"milestone_{ms_id}"
                self.cell_ids[ms_id] = cell_id
                self.cell_positions[ms_id] = {
                    "x": x, "y": y, "width": MILESTONE_SIZE, "height": MILESTONE_SIZE,
                    "center_x": x + MILESTONE_SIZE / 2, "center_y": y + MILESTONE_SIZE / 2,
                    "right_x": x + MILESTONE_SIZE, "left_x": x
                }

                # Milestone diamond
                ms_cell = ET.SubElement(root, "mxCell")
                ms_cell.set("id", cell_id)
                ms_cell.set("value", "")
                ms_cell.set("style", f"rhombus;whiteSpace=wrap;html=1;fillColor={status_color};strokeColor=#333333;strokeWidth=2;")
                ms_cell.set("vertex", "1")
                ms_cell.set("parent", "1")

                geom = ET.SubElement(ms_cell, "mxGeometry")
                geom.set("x", str(int(x)))
                geom.set("y", str(int(y)))
                geom.set("width", str(MILESTONE_SIZE))
                geom.set("height", str(MILESTONE_SIZE))
                geom.set("as", "geometry")

                # Milestone label (below the diamond)
                label_cell = ET.SubElement(root, "mxCell")
                label_cell.set("id", generate_id())
                label_cell.set("value", f"<b>{escape_xml(milestone['name'])}</b>")
                label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=top;whiteSpace=wrap;rounded=0;fontSize=9;")
                label_cell.set("vertex", "1")
                label_cell.set("parent", "1")

                label_geom = ET.SubElement(label_cell, "mxGeometry")
                label_geom.set("x", str(int(x - 30)))
                label_geom.set("y", str(int(y + MILESTONE_SIZE + 2)))
                label_geom.set("width", str(MILESTONE_SIZE + 60))
                label_geom.set("height", str(20))
                label_geom.set("as", "geometry")

                # Add risk markers if this milestone has associated risks
                self.add_risk_markers(root, ms_id, x + MILESTONE_SIZE + 2, y - 5)

    def add_risk_markers(self, root: ET.Element, item_id: str, x: float, y: float):
        """Add risk indicator circles with risk ID numbers on affected items."""
        if item_id not in self.risks_by_item:
            return

        risks = self.risks_by_item[item_id]

        # Add a marker for each risk affecting this item
        for idx, risk in enumerate(risks):
            risk_id = risk.get("id", "?")
            severity = risk.get("severity", "low")
            color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["low"])

            # Offset each marker horizontally if multiple risks
            marker_x = x + (idx * (RISK_MARKER_SIZE + 2))

            marker_cell = ET.SubElement(root, "mxCell")
            marker_cell.set("id", generate_id())
            marker_cell.set("value", str(risk_id))
            marker_cell.set("style", f"ellipse;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#333333;fontColor=#FFFFFF;fontSize=9;fontStyle=1;")
            marker_cell.set("vertex", "1")
            marker_cell.set("parent", "1")

            geom = ET.SubElement(marker_cell, "mxGeometry")
            geom.set("x", str(int(marker_x)))
            geom.set("y", str(int(y)))
            geom.set("width", str(RISK_MARKER_SIZE))
            geom.set("height", str(RISK_MARKER_SIZE))
            geom.set("as", "geometry")

    def add_dependencies(self, root: ET.Element):
        """Add dependency arrows between tasks/milestones.

        Note: Dependencies are tracked in the data model but currently hidden
        visually for cleaner diagrams. Set show_dependencies=True in project
        config to display them.
        """
        show_dependencies = self.project.get("show_dependencies", False)
        if not show_dependencies:
            return

        for dep in self.dependencies:
            from_id = dep["from"]
            to_id = dep["to"]

            if from_id not in self.cell_positions or to_id not in self.cell_positions:
                continue

            from_pos = self.cell_positions[from_id]
            to_pos = self.cell_positions[to_id]

            # Create curved arrow from right edge of source to left edge of target
            edge_cell = ET.SubElement(root, "mxCell")
            edge_cell.set("id", generate_id())
            edge_cell.set("value", "")
            edge_cell.set("style", "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#666666;strokeWidth=1;endArrow=block;endFill=1;curved=1;")
            edge_cell.set("edge", "1")
            edge_cell.set("parent", "1")
            edge_cell.set("source", self.cell_ids.get(from_id, ""))
            edge_cell.set("target", self.cell_ids.get(to_id, ""))

            geom = ET.SubElement(edge_cell, "mxGeometry")
            geom.set("relative", "1")
            geom.set("as", "geometry")

    def add_risk_table(self, root: ET.Element):
        """Add risk summary table below the timeline."""
        if not self.risks:
            return

        # Calculate Y position after milestone row and all swimlanes
        total_content_height = self.get_milestone_area_height() + sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        table_y = TIMELINE_HEADER_HEIGHT + total_content_height + TABLE_START_Y_OFFSET

        # Column widths
        col_widths = [60, 150, 80, 80, 80, 195, 250]
        headers = ["ID", "Risk Name", "Severity", "Likelihood", "Status", "Affected", "Mitigation"]

        total_width = sum(col_widths)

        # Table title
        title_cell = ET.SubElement(root, "mxCell")
        title_cell.set("id", generate_id())
        title_cell.set("value", "<b>Risk Summary</b>")
        title_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=14;fontStyle=1;")
        title_cell.set("vertex", "1")
        title_cell.set("parent", "1")

        title_geom = ET.SubElement(title_cell, "mxGeometry")
        title_geom.set("x", "10")
        title_geom.set("y", str(int(table_y - 30)))
        title_geom.set("width", "200")
        title_geom.set("height", "25")
        title_geom.set("as", "geometry")

        # Header row
        x_offset = 10
        for i, header in enumerate(headers):
            header_cell = ET.SubElement(root, "mxCell")
            header_cell.set("id", generate_id())
            header_cell.set("value", f"<b>{header}</b>")
            header_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#E0E0E0;strokeColor=#666666;fontSize=10;fontStyle=1;")
            header_cell.set("vertex", "1")
            header_cell.set("parent", "1")

            geom = ET.SubElement(header_cell, "mxGeometry")
            geom.set("x", str(x_offset))
            geom.set("y", str(int(table_y)))
            geom.set("width", str(col_widths[i]))
            geom.set("height", str(TABLE_ROW_HEIGHT))
            geom.set("as", "geometry")

            x_offset += col_widths[i]

        # Data rows
        for row_idx, risk in enumerate(self.risks):
            row_y = table_y + TABLE_ROW_HEIGHT * (row_idx + 1)
            x_offset = 10

            # Prepare affected items string
            affected = risk.get("affected", [])
            affected_names = []
            for item_id in affected:
                if item_id in self.tasks:
                    affected_names.append(self.tasks[item_id]["name"])
                elif item_id in self.milestones:
                    affected_names.append(self.milestones[item_id]["name"])
                else:
                    affected_names.append(item_id)
            affected_str = ", ".join(affected_names)

            row_data = [
                risk.get("id", ""),
                risk.get("name", ""),
                risk.get("severity", "").upper(),
                risk.get("likelihood", "").upper(),
                risk.get("status", "").replace("_", " ").title(),
                affected_str,
                risk.get("mitigation", "")
            ]

            severity = risk.get("severity", "low")
            severity_color = SEVERITY_COLORS.get(severity, "#FFFFFF")

            for col_idx, value in enumerate(row_data):
                cell = ET.SubElement(root, "mxCell")
                cell.set("id", generate_id())
                cell.set("value", escape_xml(str(value)))

                # Color the severity column
                if col_idx == 2:  # Severity column
                    style = f"rounded=0;whiteSpace=wrap;html=1;fillColor={severity_color};fillOpacity=30;strokeColor=#CCCCCC;fontSize=9;align=center;"
                else:
                    style = "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#CCCCCC;fontSize=9;align=left;spacingLeft=4;"

                cell.set("style", style)
                cell.set("vertex", "1")
                cell.set("parent", "1")

                geom = ET.SubElement(cell, "mxGeometry")
                geom.set("x", str(x_offset))
                geom.set("y", str(int(row_y)))
                geom.set("width", str(col_widths[col_idx]))
                geom.set("height", str(TABLE_ROW_HEIGHT))
                geom.set("as", "geometry")

                x_offset += col_widths[col_idx]

    def add_legend(self, root: ET.Element):
        """Add a legend explaining diagram symbols and colors."""
        # Position legend to the right of the risk table, aligned with table top
        total_content_height = self.get_milestone_area_height() + sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        legend_x = 940
        legend_y = TIMELINE_HEADER_HEIGHT + total_content_height + TABLE_START_Y_OFFSET

        # Legend container - two columns
        col_width = 180
        legend_width = col_width * 2 + 30
        legend_height = 220

        # Legend background (less rounded corners)
        bg_cell = ET.SubElement(root, "mxCell")
        bg_cell.set("id", generate_id())
        bg_cell.set("value", "")
        bg_cell.set("style", "rounded=0;arcSize=5;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#CCCCCC;strokeWidth=1;")
        bg_cell.set("vertex", "1")
        bg_cell.set("parent", "1")

        bg_geom = ET.SubElement(bg_cell, "mxGeometry")
        bg_geom.set("x", str(int(legend_x)))
        bg_geom.set("y", str(int(legend_y)))
        bg_geom.set("width", str(legend_width))
        bg_geom.set("height", str(legend_height))
        bg_geom.set("as", "geometry")

        # Legend title (centered)
        title_cell = ET.SubElement(root, "mxCell")
        title_cell.set("id", generate_id())
        title_cell.set("value", "<b>Legend</b>")
        title_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=12;fontStyle=1;")
        title_cell.set("vertex", "1")
        title_cell.set("parent", "1")

        title_geom = ET.SubElement(title_cell, "mxGeometry")
        title_geom.set("x", str(int(legend_x)))
        title_geom.set("y", str(int(legend_y + 5)))
        title_geom.set("width", str(legend_width))
        title_geom.set("height", str(20))
        title_geom.set("as", "geometry")

        # LEFT COLUMN
        left_col_x = legend_x
        current_y = legend_y + 32

        # Task Status section
        self._add_legend_section(root, left_col_x, current_y, "Task Status (Border)")
        current_y += 18

        status_items = [
            ("Complete", STATUS_COLORS["complete"]),
            ("In Progress", STATUS_COLORS["in_progress"]),
            ("At Risk", STATUS_COLORS["at_risk"]),
            ("Delayed/Blocked", STATUS_COLORS["delayed"]),
            ("Not Started", STATUS_COLORS["not_started"]),
        ]

        for label, color in status_items:
            self._add_legend_color_item(root, left_col_x + 15, current_y, label, color, "rounded=1;strokeWidth=2;fillColor=#FFFFFF;")
            current_y += 18

        current_y += 6

        # Milestone section
        self._add_legend_section(root, left_col_x, current_y, "Milestones")
        current_y += 18

        milestone_items = [
            ("On Track", STATUS_COLORS["on_track"]),
            ("At Risk", STATUS_COLORS["at_risk"]),
            ("Delayed", STATUS_COLORS["delayed"]),
        ]

        for label, color in milestone_items:
            self._add_legend_diamond_item(root, left_col_x + 15, current_y, label, color)
            current_y += 18

        # RIGHT COLUMN
        right_col_x = legend_x + col_width + 10
        current_y = legend_y + 32

        # Risk Markers section
        self._add_legend_section(root, right_col_x, current_y, "Risk Markers")
        current_y += 18

        risk_items = [
            ("High Severity", SEVERITY_COLORS["high"]),
            ("Medium Severity", SEVERITY_COLORS["medium"]),
            ("Low Severity", SEVERITY_COLORS["low"]),
        ]

        for label, color in risk_items:
            self._add_legend_circle_item(root, right_col_x + 15, current_y, label, color)
            current_y += 18

        current_y += 6

        # Other elements section
        self._add_legend_section(root, right_col_x, current_y, "Other Elements")
        current_y += 18

        # Weekend shading
        self._add_legend_rect_item(root, right_col_x + 15, current_y, "Weekend", "#E8E8E8")
        current_y += 18

        # Date marker
        self._add_legend_line_item(root, right_col_x + 15, current_y, "Date Marker", "#F44336")
        current_y += 18

        # Dependency arrow
        self._add_legend_arrow_item(root, right_col_x + 15, current_y, "Dependency", "#666666")

    def _add_legend_section(self, root: ET.Element, x: float, y: float, title: str):
        """Add a section header in the legend."""
        cell = ET.SubElement(root, "mxCell")
        cell.set("id", generate_id())
        cell.set("value", f"<b>{escape_xml(title)}</b>")
        cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=10;fontColor=#666666;")
        cell.set("vertex", "1")
        cell.set("parent", "1")

        geom = ET.SubElement(cell, "mxGeometry")
        geom.set("x", str(int(x + 10)))
        geom.set("y", str(int(y)))
        geom.set("width", str(200))
        geom.set("height", str(16))
        geom.set("as", "geometry")

    def _add_legend_color_item(self, root: ET.Element, x: float, y: float, label: str, color: str, extra_style: str = ""):
        """Add a colored rectangle item to the legend."""
        # Color swatch
        swatch = ET.SubElement(root, "mxCell")
        swatch.set("id", generate_id())
        swatch.set("value", "")
        swatch.set("style", f"{extra_style}strokeColor={color};")
        swatch.set("vertex", "1")
        swatch.set("parent", "1")

        swatch_geom = ET.SubElement(swatch, "mxGeometry")
        swatch_geom.set("x", str(int(x)))
        swatch_geom.set("y", str(int(y)))
        swatch_geom.set("width", str(24))
        swatch_geom.set("height", str(14))
        swatch_geom.set("as", "geometry")

        # Label
        label_cell = ET.SubElement(root, "mxCell")
        label_cell.set("id", generate_id())
        label_cell.set("value", escape_xml(label))
        label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;")
        label_cell.set("vertex", "1")
        label_cell.set("parent", "1")

        label_geom = ET.SubElement(label_cell, "mxGeometry")
        label_geom.set("x", str(int(x + 30)))
        label_geom.set("y", str(int(y)))
        label_geom.set("width", str(120))
        label_geom.set("height", str(14))
        label_geom.set("as", "geometry")

    def _add_legend_diamond_item(self, root: ET.Element, x: float, y: float, label: str, color: str):
        """Add a diamond (milestone) item to the legend."""
        # Diamond shape
        diamond = ET.SubElement(root, "mxCell")
        diamond.set("id", generate_id())
        diamond.set("value", "")
        diamond.set("style", f"rhombus;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#333333;strokeWidth=1;")
        diamond.set("vertex", "1")
        diamond.set("parent", "1")

        diamond_geom = ET.SubElement(diamond, "mxGeometry")
        diamond_geom.set("x", str(int(x + 4)))
        diamond_geom.set("y", str(int(y)))
        diamond_geom.set("width", str(14))
        diamond_geom.set("height", str(14))
        diamond_geom.set("as", "geometry")

        # Label
        label_cell = ET.SubElement(root, "mxCell")
        label_cell.set("id", generate_id())
        label_cell.set("value", escape_xml(label))
        label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;")
        label_cell.set("vertex", "1")
        label_cell.set("parent", "1")

        label_geom = ET.SubElement(label_cell, "mxGeometry")
        label_geom.set("x", str(int(x + 30)))
        label_geom.set("y", str(int(y)))
        label_geom.set("width", str(120))
        label_geom.set("height", str(14))
        label_geom.set("as", "geometry")

    def _add_legend_circle_item(self, root: ET.Element, x: float, y: float, label: str, color: str):
        """Add a circle (risk marker) item to the legend."""
        # Circle shape with number
        circle = ET.SubElement(root, "mxCell")
        circle.set("id", generate_id())
        circle.set("value", "#")
        circle.set("style", f"ellipse;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#333333;fontColor=#FFFFFF;fontSize=8;fontStyle=1;")
        circle.set("vertex", "1")
        circle.set("parent", "1")

        circle_geom = ET.SubElement(circle, "mxGeometry")
        circle_geom.set("x", str(int(x + 2)))
        circle_geom.set("y", str(int(y)))
        circle_geom.set("width", str(16))
        circle_geom.set("height", str(16))
        circle_geom.set("as", "geometry")

        # Label
        label_cell = ET.SubElement(root, "mxCell")
        label_cell.set("id", generate_id())
        label_cell.set("value", escape_xml(label))
        label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;")
        label_cell.set("vertex", "1")
        label_cell.set("parent", "1")

        label_geom = ET.SubElement(label_cell, "mxGeometry")
        label_geom.set("x", str(int(x + 30)))
        label_geom.set("y", str(int(y)))
        label_geom.set("width", str(120))
        label_geom.set("height", str(16))
        label_geom.set("as", "geometry")

    def _add_legend_rect_item(self, root: ET.Element, x: float, y: float, label: str, color: str):
        """Add a rectangle item to the legend."""
        # Rectangle
        rect = ET.SubElement(root, "mxCell")
        rect.set("id", generate_id())
        rect.set("value", "")
        rect.set("style", f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};strokeColor=none;opacity=70;")
        rect.set("vertex", "1")
        rect.set("parent", "1")

        rect_geom = ET.SubElement(rect, "mxGeometry")
        rect_geom.set("x", str(int(x)))
        rect_geom.set("y", str(int(y)))
        rect_geom.set("width", str(24))
        rect_geom.set("height", str(14))
        rect_geom.set("as", "geometry")

        # Label
        label_cell = ET.SubElement(root, "mxCell")
        label_cell.set("id", generate_id())
        label_cell.set("value", escape_xml(label))
        label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;")
        label_cell.set("vertex", "1")
        label_cell.set("parent", "1")

        label_geom = ET.SubElement(label_cell, "mxGeometry")
        label_geom.set("x", str(int(x + 30)))
        label_geom.set("y", str(int(y)))
        label_geom.set("width", str(120))
        label_geom.set("height", str(14))
        label_geom.set("as", "geometry")

    def _add_legend_line_item(self, root: ET.Element, x: float, y: float, label: str, color: str):
        """Add a vertical dashed line item to the legend."""
        # Vertical line
        line = ET.SubElement(root, "mxCell")
        line.set("id", generate_id())
        line.set("value", "")
        line.set("style", f"endArrow=none;html=1;strokeColor={color};strokeWidth=2;dashed=1;dashPattern=8 4;")
        line.set("edge", "1")
        line.set("parent", "1")

        line_geom = ET.SubElement(line, "mxGeometry")
        line_geom.set("relative", "1")
        line_geom.set("as", "geometry")

        source = ET.SubElement(line_geom, "mxPoint")
        source.set("x", str(int(x + 12)))
        source.set("y", str(int(y)))
        source.set("as", "sourcePoint")

        target = ET.SubElement(line_geom, "mxPoint")
        target.set("x", str(int(x + 12)))
        target.set("y", str(int(y + 12)))
        target.set("as", "targetPoint")

        # Label
        label_cell = ET.SubElement(root, "mxCell")
        label_cell.set("id", generate_id())
        label_cell.set("value", escape_xml(label))
        label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;")
        label_cell.set("vertex", "1")
        label_cell.set("parent", "1")

        label_geom = ET.SubElement(label_cell, "mxGeometry")
        label_geom.set("x", str(int(x + 30)))
        label_geom.set("y", str(int(y)))
        label_geom.set("width", str(100))
        label_geom.set("height", str(14))
        label_geom.set("as", "geometry")

    def _add_legend_arrow_item(self, root: ET.Element, x: float, y: float, label: str, color: str):
        """Add an arrow item to the legend."""
        # Horizontal arrow
        arrow = ET.SubElement(root, "mxCell")
        arrow.set("id", generate_id())
        arrow.set("value", "")
        arrow.set("style", f"endArrow=block;html=1;strokeColor={color};strokeWidth=1;endFill=1;curved=1;")
        arrow.set("edge", "1")
        arrow.set("parent", "1")

        arrow_geom = ET.SubElement(arrow, "mxGeometry")
        arrow_geom.set("relative", "1")
        arrow_geom.set("as", "geometry")

        source = ET.SubElement(arrow_geom, "mxPoint")
        source.set("x", str(int(x)))
        source.set("y", str(int(y + 7)))
        source.set("as", "sourcePoint")

        target = ET.SubElement(arrow_geom, "mxPoint")
        target.set("x", str(int(x + 24)))
        target.set("y", str(int(y + 7)))
        target.set("as", "targetPoint")

        # Label
        label_cell = ET.SubElement(root, "mxCell")
        label_cell.set("id", generate_id())
        label_cell.set("value", escape_xml(label))
        label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;")
        label_cell.set("vertex", "1")
        label_cell.set("parent", "1")

        label_geom = ET.SubElement(label_cell, "mxGeometry")
        label_geom.set("x", str(int(x + 30)))
        label_geom.set("y", str(int(y)))
        label_geom.set("width", str(100))
        label_geom.set("height", str(14))
        label_geom.set("as", "geometry")

    def generate(self) -> str:
        """Generate the complete draw.io XML."""
        mxfile = self.create_mxfile()
        diagram = self.create_diagram(mxfile)
        model = self.create_graph_model(diagram)

        root = ET.SubElement(model, "root")

        self.add_root_cells(root)
        self.add_timeline_header(root)
        self.add_swimlanes(root)
        self.add_milestone_row(root)
        self.add_weekend_shading(root)
        self.add_date_markers(root)
        self.add_tasks(root)
        self.add_milestones(root)
        self.add_dependencies(root)
        self.add_risk_table(root)
        self.add_legend(root)
        self.add_date_marker_labels(root)  # Added last to appear on top

        # Convert to string with XML declaration
        ET.indent(mxfile, space="  ")
        xml_str = ET.tostring(mxfile, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str


def load_project_data(filepath: str) -> dict:
    """Load and validate project JSON data."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Basic validation
    if "project" not in data:
        raise ValueError("Missing 'project' section in JSON")

    project = data["project"]
    required_fields = ["start_date", "end_date"]
    for field in required_fields:
        if field not in project:
            raise ValueError(f"Missing required field 'project.{field}'")

    # Validate dates
    try:
        parse_date(project["start_date"])
        parse_date(project["end_date"])
    except ValueError as e:
        raise ValueError(f"Invalid date format: {e}")

    return data


def process_single_file(input_path: str, output_path: str = None) -> bool:
    """Process a single project JSON file and generate draw.io output.

    Returns True on success, False on failure.
    """
    global _id_counter
    _id_counter = 0  # Reset ID counter for each file

    # Auto-generate output path if not specified
    if output_path is None:
        base_name = os.path.splitext(input_path)[0]
        output_path = f"{base_name}.drawio"

    try:
        # Load project data
        print(f"\nProcessing: {input_path}")
        data = load_project_data(input_path)

        # Generate draw.io XML
        generator = DrawioGenerator(data)
        xml_output = generator.generate()

        # Write output file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml_output)

        print(f"  -> Generated: {output_path}")

        # Print summary
        num_tasks = len(data.get("tasks", []))
        num_milestones = len(data.get("milestones", []))
        num_workstreams = len(data.get("workstreams", []))
        num_risks = len(data.get("risks", []))
        num_markers = len(data.get("date_markers", []))

        print(f"     {num_workstreams} workstreams, {num_tasks} tasks, {num_milestones} milestones, {num_markers} markers, {num_risks} risks")
        return True

    except FileNotFoundError:
        print(f"  Error: File not found", file=sys.stderr)
        return False
    except json.JSONDecodeError as e:
        print(f"  Error: Invalid JSON - {e}", file=sys.stderr)
        return False
    except ValueError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Generate draw.io swimlane timeline diagrams from project JSON data.",
        epilog="""
Examples:
  %(prog)s project.json                     # Single file -> project.drawio
  %(prog)s project.json -o output.drawio    # Single file with custom output
  %(prog)s proj1.json proj2.json proj3.json # Multiple files -> proj1.drawio, proj2.drawio, proj3.drawio
  %(prog)s --dir ./projects                 # All JSON files in directory
  %(prog)s --dir ./projects -o ./output     # All JSON files, output to different directory
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Input project JSON file(s)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path. For single file: output filename. For multiple files/directory: output directory."
    )
    parser.add_argument(
        "-d", "--dir",
        help="Process all .json files in the specified directory"
    )

    args = parser.parse_args()

    # Collect input files
    input_files = []

    if args.dir:
        # Process directory
        if not os.path.isdir(args.dir):
            print(f"Error: Directory '{args.dir}' not found", file=sys.stderr)
            sys.exit(1)

        for filename in sorted(os.listdir(args.dir)):
            if filename.endswith(".json"):
                input_files.append(os.path.join(args.dir, filename))

        if not input_files:
            print(f"Error: No .json files found in '{args.dir}'", file=sys.stderr)
            sys.exit(1)
    elif args.inputs:
        input_files = args.inputs
    else:
        parser.print_help()
        sys.exit(1)

    # Process files
    success_count = 0
    fail_count = 0

    if len(input_files) == 1 and args.output and not os.path.isdir(args.output):
        # Single file with specific output filename
        if process_single_file(input_files[0], args.output):
            success_count += 1
        else:
            fail_count += 1
    else:
        # Multiple files or directory output
        output_dir = args.output if args.output else None

        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        for input_path in input_files:
            if output_dir:
                base_name = os.path.splitext(os.path.basename(input_path))[0]
                output_path = os.path.join(output_dir, f"{base_name}.drawio")
            else:
                output_path = None  # Auto-generate in same directory as input

            if process_single_file(input_path, output_path):
                success_count += 1
            else:
                fail_count += 1

    # Summary
    print(f"\n{'='*40}")
    print(f"Processed {success_count + fail_count} file(s): {success_count} succeeded, {fail_count} failed")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

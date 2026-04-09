"""
Northbreeze-compatible OData V4 service.

A real OData V4 service modeled after SAP's Northbreeze tutorial, implementing:
- $metadata endpoint (CSDL XML)
- Service document
- Entity sets: Products, Categories, Suppliers
- Navigation properties
- $filter, $select, $top, $skip, $orderby, $count, $expand
- Single entity by key: /Products(1)
"""

import json
import re

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# ── Seed Data ─────────────────────────────────────────────────────────────────

CATEGORIES = [
    {"ID": 1, "Name": "Beverages", "Description": "Soft drinks, coffees, teas, beers, and ales"},
    {
        "ID": 2,
        "Name": "Condiments",
        "Description": "Sweet and savory sauces, relishes, spreads, and seasonings",
    },
    {"ID": 3, "Name": "Confections", "Description": "Desserts, candies, and sweet breads"},
    {"ID": 4, "Name": "Dairy Products", "Description": "Cheeses and dairy-based items"},
    {"ID": 5, "Name": "Grains/Cereals", "Description": "Breads, crackers, pasta, and cereal"},
    {"ID": 6, "Name": "Meat/Poultry", "Description": "Prepared meats and poultry products"},
    {"ID": 7, "Name": "Produce", "Description": "Dried fruit and bean curd"},
    {"ID": 8, "Name": "Seafood", "Description": "Seaweed and fish"},
]

SUPPLIERS = [
    {
        "ID": 1,
        "Name": "Exotic Liquids",
        "City": "London",
        "Country": "UK",
        "Phone": "(171) 555-2222",
    },
    {
        "ID": 2,
        "Name": "New Orleans Cajun",
        "City": "New Orleans",
        "Country": "USA",
        "Phone": "(100) 555-4822",
    },
    {
        "ID": 3,
        "Name": "Grandma Kelly's",
        "City": "Ann Arbor",
        "Country": "USA",
        "Phone": "(313) 555-5735",
    },
    {
        "ID": 4,
        "Name": "Tokyo Traders",
        "City": "Tokyo",
        "Country": "Japan",
        "Phone": "(03) 3555-5011",
    },
    {"ID": 5, "Name": "Mayumi's", "City": "Osaka", "Country": "Japan", "Phone": "(06) 431-7877"},
    {
        "ID": 6,
        "Name": "Pavlova Ltd.",
        "City": "Melbourne",
        "Country": "Australia",
        "Phone": "(03) 444-2343",
    },
]

PRODUCTS = [
    {
        "ID": 1,
        "Name": "Chai",
        "Price": 18.00,
        "Stock": 39,
        "CategoryID": 1,
        "SupplierID": 1,
        "Discontinued": False,
    },
    {
        "ID": 2,
        "Name": "Chang",
        "Price": 19.00,
        "Stock": 17,
        "CategoryID": 1,
        "SupplierID": 1,
        "Discontinued": False,
    },
    {
        "ID": 3,
        "Name": "Aniseed Syrup",
        "Price": 10.00,
        "Stock": 13,
        "CategoryID": 2,
        "SupplierID": 1,
        "Discontinued": False,
    },
    {
        "ID": 4,
        "Name": "Chef Anton's Cajun",
        "Price": 22.00,
        "Stock": 53,
        "CategoryID": 2,
        "SupplierID": 2,
        "Discontinued": False,
    },
    {
        "ID": 5,
        "Name": "Grandma's Spread",
        "Price": 25.00,
        "Stock": 120,
        "CategoryID": 2,
        "SupplierID": 3,
        "Discontinued": False,
    },
    {
        "ID": 6,
        "Name": "Uncle Bob's Pears",
        "Price": 30.00,
        "Stock": 15,
        "CategoryID": 7,
        "SupplierID": 3,
        "Discontinued": False,
    },
    {
        "ID": 7,
        "Name": "Northwoods Sauce",
        "Price": 40.00,
        "Stock": 6,
        "CategoryID": 2,
        "SupplierID": 3,
        "Discontinued": False,
    },
    {
        "ID": 8,
        "Name": "Mishi Kobe Niku",
        "Price": 97.00,
        "Stock": 29,
        "CategoryID": 6,
        "SupplierID": 4,
        "Discontinued": True,
    },
    {
        "ID": 9,
        "Name": "Ikura",
        "Price": 31.00,
        "Stock": 31,
        "CategoryID": 8,
        "SupplierID": 4,
        "Discontinued": False,
    },
    {
        "ID": 10,
        "Name": "Queso Cabrales",
        "Price": 21.00,
        "Stock": 22,
        "CategoryID": 4,
        "SupplierID": 5,
        "Discontinued": False,
    },
    {
        "ID": 11,
        "Name": "Queso Manchego",
        "Price": 38.00,
        "Stock": 86,
        "CategoryID": 4,
        "SupplierID": 5,
        "Discontinued": False,
    },
    {
        "ID": 12,
        "Name": "Konbu",
        "Price": 6.00,
        "Stock": 24,
        "CategoryID": 8,
        "SupplierID": 6,
        "Discontinued": False,
    },
    {
        "ID": 13,
        "Name": "Tofu",
        "Price": 23.25,
        "Stock": 35,
        "CategoryID": 7,
        "SupplierID": 6,
        "Discontinued": False,
    },
    {
        "ID": 14,
        "Name": "Genen Shouyu",
        "Price": 15.50,
        "Stock": 39,
        "CategoryID": 2,
        "SupplierID": 6,
        "Discontinued": False,
    },
    {
        "ID": 15,
        "Name": "Pavlova",
        "Price": 17.45,
        "Stock": 29,
        "CategoryID": 3,
        "SupplierID": 6,
        "Discontinued": False,
    },
]

NAMESPACE = "Northbreeze"
SERVICE_URL = "/odata/v4/northbreeze"

# ── $metadata ─────────────────────────────────────────────────────────────────

METADATA_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="{NAMESPACE}" xmlns="http://docs.oasis-open.org/odata/ns/edm">

      <EntityType Name="Product">
        <Key><PropertyRef Name="ID"/></Key>
        <Property Name="ID" Type="Edm.Int32" Nullable="false"/>
        <Property Name="Name" Type="Edm.String"/>
        <Property Name="Price" Type="Edm.Decimal" Scale="2"/>
        <Property Name="Stock" Type="Edm.Int32"/>
        <Property Name="CategoryID" Type="Edm.Int32"/>
        <Property Name="SupplierID" Type="Edm.Int32"/>
        <Property Name="Discontinued" Type="Edm.Boolean"/>
        <NavigationProperty Name="Category" Type="{NAMESPACE}.Category" Partner="Products">
          <ReferentialConstraint Property="CategoryID" ReferencedProperty="ID"/>
        </NavigationProperty>
        <NavigationProperty Name="Supplier" Type="{NAMESPACE}.Supplier" Partner="Products">
          <ReferentialConstraint Property="SupplierID" ReferencedProperty="ID"/>
        </NavigationProperty>
      </EntityType>

      <EntityType Name="Category">
        <Key><PropertyRef Name="ID"/></Key>
        <Property Name="ID" Type="Edm.Int32" Nullable="false"/>
        <Property Name="Name" Type="Edm.String"/>
        <Property Name="Description" Type="Edm.String"/>
        <NavigationProperty
          Name="Products"
          Type="Collection({NAMESPACE}.Product)"
          Partner="Category"
        />
      </EntityType>

      <EntityType Name="Supplier">
        <Key><PropertyRef Name="ID"/></Key>
        <Property Name="ID" Type="Edm.Int32" Nullable="false"/>
        <Property Name="Name" Type="Edm.String"/>
        <Property Name="City" Type="Edm.String"/>
        <Property Name="Country" Type="Edm.String"/>
        <Property Name="Phone" Type="Edm.String"/>
        <NavigationProperty
          Name="Products"
          Type="Collection({NAMESPACE}.Product)"
          Partner="Supplier"
        />
      </EntityType>

      <Function Name="GetTopProducts" IsBound="false">
        <Parameter Name="count" Type="Edm.Int32"/>
        <ReturnType Type="Collection({NAMESPACE}.Product)"/>
      </Function>

      <Action Name="ResetData" IsBound="false"/>

      <EntityContainer Name="NorthbreezeService">
        <EntitySet Name="Products" EntityType="{NAMESPACE}.Product">
          <NavigationPropertyBinding Path="Category" Target="Categories"/>
          <NavigationPropertyBinding Path="Supplier" Target="Suppliers"/>
        </EntitySet>
        <EntitySet Name="Categories" EntityType="{NAMESPACE}.Category">
          <NavigationPropertyBinding Path="Products" Target="Products"/>
        </EntitySet>
        <EntitySet Name="Suppliers" EntityType="{NAMESPACE}.Supplier">
          <NavigationPropertyBinding Path="Products" Target="Products"/>
        </EntitySet>
        <FunctionImport
          Name="GetTopProducts"
          Function="{NAMESPACE}.GetTopProducts"
          EntitySet="Products"
        />
        <ActionImport Name="ResetData" Action="{NAMESPACE}.ResetData"/>
      </EntityContainer>

    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""


# ── Helpers ──────────────────────────────────────────────────────────────────


def odata_response(value, context=None, count=None):
    body = {}
    if context:
        body["@odata.context"] = context
    if count is not None:
        body["@odata.count"] = count
    body["value"] = value
    return Response(
        json.dumps(body, default=str), content_type="application/json;odata.metadata=minimal"
    )


def odata_entity(entity, context=None):
    body = dict(entity)
    if context:
        body["@odata.context"] = context
    return Response(
        json.dumps(body, default=str), content_type="application/json;odata.metadata=minimal"
    )


def apply_filter(items, filter_str):
    """Basic OData $filter support: eq, ne, gt, lt, ge, le, contains, startswith."""
    if not filter_str:
        return items
    # Guard against pathologically long filter strings (ReDoS mitigation).
    if len(filter_str) > 500:
        return items

    # eq
    m = re.match(r"(\w+)\s+eq\s+'([^']*)'", filter_str)
    if m:
        field, val = m.group(1), m.group(2)
        return [i for i in items if str(i.get(field, "")) == val]
    m = re.match(r"(\w+)\s+eq\s+(\d+(?:\.\d+)?)\Z", filter_str)
    if m:
        field, val = m.group(1), float(m.group(2))
        return [i for i in items if i.get(field) == (int(val) if val == int(val) else val)]
    # gt / lt / ge / le
    for op, fn in [
        ("gt", lambda a, b: a > b),
        ("lt", lambda a, b: a < b),
        ("ge", lambda a, b: a >= b),
        ("le", lambda a, b: a <= b),
    ]:
        m = re.match(rf"(\w+)\s+{op}\s+(\d+(?:\.\d+)?)", filter_str)
        if m:
            field, val = m.group(1), float(m.group(2))
            return [i for i in items if fn(i.get(field, 0), val)]
    # contains
    m = re.match(r"contains\((\w+),\s*'([^']*)'\)", filter_str)
    if m:
        field, val = m.group(1), m.group(2).lower()
        return [i for i in items if val in str(i.get(field, "")).lower()]
    # startswith
    m = re.match(r"startswith\((\w+),\s*'([^']*)'\)", filter_str)
    if m:
        field, val = m.group(1), m.group(2).lower()
        return [i for i in items if str(i.get(field, "")).lower().startswith(val)]

    return items


def apply_select(items, select_str):
    if not select_str:
        return items
    fields = [f.strip() for f in select_str.split(",")]
    return [{k: v for k, v in item.items() if k in fields} for item in items]


def apply_orderby(items, orderby_str):
    if not orderby_str:
        return items
    parts = orderby_str.strip().split()
    field = parts[0]
    desc = len(parts) > 1 and parts[1].lower() == "desc"
    return sorted(items, key=lambda x: x.get(field, 0), reverse=desc)


def apply_query_options(items):
    """Apply standard OData query options."""
    items = apply_filter(items, request.args.get("$filter"))
    items = apply_orderby(items, request.args.get("$orderby"))
    total_count = len(items) if request.args.get("$count", "").lower() == "true" else None
    skip = int(request.args.get("$skip", 0))
    top = request.args.get("$top")
    items = items[skip:]
    if top:
        items = items[: int(top)]
    items = apply_select(items, request.args.get("$select"))
    return items, total_count


def expand_entity(entity, entity_set_name):
    """Handle $expand for navigation properties."""
    expand = request.args.get("$expand")
    if not expand:
        return entity
    entity = dict(entity)
    expand_fields = [f.strip() for f in expand.split(",")]
    for field in expand_fields:
        if entity_set_name == "Products":
            if field == "Category":
                cat_id = entity.get("CategoryID")
                entity["Category"] = next((c for c in CATEGORIES if c["ID"] == cat_id), None)
            elif field == "Supplier":
                sup_id = entity.get("SupplierID")
                entity["Supplier"] = next((s for s in SUPPLIERS if s["ID"] == sup_id), None)
        elif entity_set_name == "Categories":
            if field == "Products":
                entity["Products"] = [p for p in PRODUCTS if p["CategoryID"] == entity["ID"]]
        elif entity_set_name == "Suppliers":
            if field == "Products":
                entity["Products"] = [p for p in PRODUCTS if p["SupplierID"] == entity["ID"]]
    return entity


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route(f"{SERVICE_URL}/$metadata")
def metadata():
    return Response(METADATA_XML, content_type="application/xml")


@app.route(f"{SERVICE_URL}/")
@app.route(f"{SERVICE_URL}")
def service_document():
    return jsonify(
        {
            "@odata.context": f"{SERVICE_URL}/$metadata",
            "value": [
                {"name": "Products", "url": "Products"},
                {"name": "Categories", "url": "Categories"},
                {"name": "Suppliers", "url": "Suppliers"},
            ],
        }
    )


# Entity sets
@app.route(f"{SERVICE_URL}/Products")
def products_list():
    items = [expand_entity(p, "Products") for p in PRODUCTS]
    items, count = apply_query_options(items)
    return odata_response(items, f"{SERVICE_URL}/$metadata#Products", count)


@app.route(f"{SERVICE_URL}/Products(<int:key>)")
def product_by_key(key):
    item = next((p for p in PRODUCTS if p["ID"] == key), None)
    if not item:
        return jsonify({"error": {"code": "404", "message": f"Product({key}) not found"}}), 404
    item = expand_entity(item, "Products")
    return odata_entity(item, f"{SERVICE_URL}/$metadata#Products/$entity")


@app.route(f"{SERVICE_URL}/Categories")
def categories_list():
    items = [expand_entity(c, "Categories") for c in CATEGORIES]
    items, count = apply_query_options(items)
    return odata_response(items, f"{SERVICE_URL}/$metadata#Categories", count)


@app.route(f"{SERVICE_URL}/Categories(<int:key>)")
def category_by_key(key):
    item = next((c for c in CATEGORIES if c["ID"] == key), None)
    if not item:
        return jsonify({"error": {"code": "404", "message": f"Category({key}) not found"}}), 404
    item = expand_entity(item, "Categories")
    return odata_entity(item, f"{SERVICE_URL}/$metadata#Categories/$entity")


@app.route(f"{SERVICE_URL}/Suppliers")
def suppliers_list():
    items = [expand_entity(s, "Suppliers") for s in SUPPLIERS]
    items, count = apply_query_options(items)
    return odata_response(items, f"{SERVICE_URL}/$metadata#Suppliers", count)


@app.route(f"{SERVICE_URL}/Suppliers(<int:key>)")
def supplier_by_key(key):
    item = next((s for s in SUPPLIERS if s["ID"] == key), None)
    if not item:
        return jsonify({"error": {"code": "404", "message": f"Supplier({key}) not found"}}), 404
    item = expand_entity(item, "Suppliers")
    return odata_entity(item, f"{SERVICE_URL}/$metadata#Suppliers/$entity")


# Navigation: Products(1)/Category
@app.route(f"{SERVICE_URL}/Products(<int:key>)/Category")
def product_category(key):
    product = next((p for p in PRODUCTS if p["ID"] == key), None)
    if not product:
        return jsonify({"error": {"code": "404", "message": "Product not found"}}), 404
    cat = next((c for c in CATEGORIES if c["ID"] == product["CategoryID"]), None)
    return odata_entity(cat, f"{SERVICE_URL}/$metadata#Categories/$entity")


@app.route(f"{SERVICE_URL}/Products(<int:key>)/Supplier")
def product_supplier(key):
    product = next((p for p in PRODUCTS if p["ID"] == key), None)
    if not product:
        return jsonify({"error": {"code": "404", "message": "Product not found"}}), 404
    sup = next((s for s in SUPPLIERS if s["ID"] == product["SupplierID"]), None)
    return odata_entity(sup, f"{SERVICE_URL}/$metadata#Suppliers/$entity")


@app.route(f"{SERVICE_URL}/Categories(<int:key>)/Products")
def category_products(key):
    items = [p for p in PRODUCTS if p["CategoryID"] == key]
    items, count = apply_query_options(items)
    return odata_response(items, f"{SERVICE_URL}/$metadata#Products", count)


# Function import
@app.route(f"{SERVICE_URL}/GetTopProducts(count=<int:count>)")
def get_top_products(count):
    sorted_products = sorted(PRODUCTS, key=lambda p: p["Price"], reverse=True)
    return odata_response(sorted_products[:count], f"{SERVICE_URL}/$metadata#Products")


# Action import
@app.route(f"{SERVICE_URL}/ResetData", methods=["POST"])
def reset_data():
    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4004)

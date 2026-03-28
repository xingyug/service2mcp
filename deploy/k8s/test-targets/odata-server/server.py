from flask import Flask, jsonify, request, Response

app = Flask(__name__)

PRODUCTS = [
    {"Id": 1, "Name": "Widget", "Price": 9.99, "Category": "Electronics"},
    {"Id": 2, "Name": "Gadget", "Price": 19.99, "Category": "Electronics"},
    {"Id": 3, "Name": "Doohickey", "Price": 4.99, "Category": "Accessories"},
]

CATEGORIES = [
    {"Id": 1, "Name": "Electronics"},
    {"Id": 2, "Name": "Accessories"},
]

METADATA_XML = '''\
<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="TestService" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="Product">
        <Key><PropertyRef Name="Id"/></Key>
        <Property Name="Id" Type="Edm.Int32" Nullable="false"/>
        <Property Name="Name" Type="Edm.String"/>
        <Property Name="Price" Type="Edm.Decimal"/>
        <Property Name="Category" Type="Edm.String"/>
      </EntityType>
      <EntityType Name="Category">
        <Key><PropertyRef Name="Id"/></Key>
        <Property Name="Id" Type="Edm.Int32" Nullable="false"/>
        <Property Name="Name" Type="Edm.String"/>
      </EntityType>
      <Function Name="GetTopProducts" IsBound="false">
        <ReturnType Type="Collection(TestService.Product)"/>
      </Function>
      <Action Name="ResetProductData" IsBound="false"/>
      <EntityContainer Name="TestContainer">
        <EntitySet Name="Products" EntityType="TestService.Product"/>
        <EntitySet Name="Categories" EntityType="TestService.Category"/>
        <FunctionImport Name="GetTopProducts" Function="TestService.GetTopProducts" EntitySet="Products"/>
        <ActionImport Name="ResetProductData" Action="TestService.ResetProductData"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>'''


@app.route('/healthz')
def healthz():
    return 'ok', 200


@app.route('/odata/$metadata')
def metadata():
    return Response(METADATA_XML, content_type='application/xml')


@app.route('/odata/')
def service_document():
    return jsonify({
        "@odata.context": "$metadata",
        "value": [
            {"name": "Products", "kind": "EntitySet", "url": "Products"},
            {"name": "Categories", "kind": "EntitySet", "url": "Categories"},
        ]
    })


@app.route('/odata/Products', methods=['GET', 'POST'])
def products_collection():
    if request.method == 'GET':
        return jsonify({
            "@odata.context": "$metadata#Products",
            "value": PRODUCTS,
        })
    body = request.get_json(force=True)
    new_id = max(p["Id"] for p in PRODUCTS) + 1 if PRODUCTS else 1
    product = {"Id": new_id, "Name": body.get("Name"), "Price": body.get("Price"), "Category": body.get("Category")}
    PRODUCTS.append(product)
    return jsonify(product), 201


@app.route('/odata/Products(<int:product_id>)', methods=['GET', 'PUT', 'DELETE'])
def product_item(product_id):
    product = next((p for p in PRODUCTS if p["Id"] == product_id), None)
    if product is None:
        return jsonify({"error": {"code": "404", "message": "Product not found"}}), 404

    if request.method == 'GET':
        return jsonify({"@odata.context": "$metadata#Products/$entity", **product})

    if request.method == 'PUT':
        body = request.get_json(force=True)
        product.update({k: body[k] for k in ("Name", "Price", "Category") if k in body})
        return jsonify(product)

    # DELETE
    PRODUCTS[:] = [p for p in PRODUCTS if p["Id"] != product_id]
    return '', 204


@app.route('/odata/Categories')
def categories_collection():
    return jsonify({
        "@odata.context": "$metadata#Categories",
        "value": CATEGORIES,
    })


@app.route('/odata/GetTopProducts()')
def get_top_products():
    top = sorted(PRODUCTS, key=lambda p: p["Price"], reverse=True)[:2]
    return jsonify({
        "@odata.context": "$metadata#Products",
        "value": top,
    })


@app.route('/odata/ResetProductData', methods=['POST'])
def reset_product_data():
    PRODUCTS[:] = [
        {"Id": 1, "Name": "Widget", "Price": 9.99, "Category": "Electronics"},
        {"Id": 2, "Name": "Gadget", "Price": 19.99, "Category": "Electronics"},
        {"Id": 3, "Name": "Doohickey", "Price": 4.99, "Category": "Accessories"},
    ]
    return '', 204


if __name__ == '__main__':
    print("OData mock server listening on :8000")
    app.run(host='0.0.0.0', port=8000)

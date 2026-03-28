package com.example.soap;

import com.example.order.generated.*;
import jakarta.jws.WebService;
import org.springframework.stereotype.Service;

import javax.xml.datatype.DatatypeFactory;
import javax.xml.datatype.XMLGregorianCalendar;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

@Service
@WebService(
    endpointInterface = "com.example.order.generated.OrderPortType",
    serviceName = "OrderService",
    portName = "OrderPort",
    targetNamespace = "http://example.com/order",
    wsdlLocation = "classpath:wsdl/OrderService.wsdl"
)
public class OrderServiceImpl implements OrderPortType {

    private final Map<String, OrderRecord> orders = new ConcurrentHashMap<>();
    private final AtomicInteger counter = new AtomicInteger(1000);

    public OrderServiceImpl() {
        seedData();
    }

    private void seedData() {
        addSeedOrder("ORD-1001", "CUST-001", OrderStatus.DELIVERED, Priority.STANDARD,
            List.of(item("SKU-PHONE-001", "Galaxy Pro Max", 1, 99900)),
            address("123 Main St", "New York", "NY", "10001", "US"));
        addSeedOrder("ORD-1002", "CUST-001", OrderStatus.SHIPPED, Priority.EXPRESS,
            List.of(item("SKU-LAPTOP-001", "ThinkPad X1", 1, 169900)),
            address("123 Main St", "New York", "NY", "10001", "US"));
        addSeedOrder("ORD-1003", "CUST-002", OrderStatus.PROCESSING, Priority.STANDARD,
            List.of(item("SKU-BOOK-001", "Pragmatic Programmer", 2, 4999),
                    item("SKU-BOOK-002", "DDIA", 1, 4499)),
            address("456 Oak Ave", "Chicago", "IL", "60601", "US"));
        addSeedOrder("ORD-1004", "CUST-003", OrderStatus.PENDING, Priority.OVERNIGHT,
            List.of(item("SKU-HOME-002", "Robot Vacuum Pro", 1, 49900)),
            address("10 Downing St", "London", null, "SW1A 2AA", "GB"));
        addSeedOrder("ORD-1005", "CUST-004", OrderStatus.CANCELLED, Priority.STANDARD,
            List.of(item("SKU-SHOE-001", "UltraBoost Running", 1, 18000)),
            address("1-1 Chiyoda", "Tokyo", null, "100-0001", "JP"));
    }

    private void addSeedOrder(String id, String customerId, OrderStatus status, Priority priority,
                              List<OrderLineItem> items, Address addr) {
        OrderRecord rec = new OrderRecord();
        rec.orderId = id;
        rec.customerId = customerId;
        rec.status = status;
        rec.priority = priority;
        rec.items = items;
        rec.address = addr;
        rec.notes = null;
        rec.history = new ArrayList<>();
        rec.history.add(historyEntry(OrderStatus.PENDING, "Order placed"));
        if (status != OrderStatus.PENDING) {
            rec.history.add(historyEntry(OrderStatus.CONFIRMED, "Payment confirmed"));
        }
        if (status == OrderStatus.PROCESSING || status == OrderStatus.SHIPPED || status == OrderStatus.DELIVERED) {
            rec.history.add(historyEntry(OrderStatus.PROCESSING, "Being prepared"));
        }
        if (status == OrderStatus.SHIPPED || status == OrderStatus.DELIVERED) {
            rec.history.add(historyEntry(OrderStatus.SHIPPED, "Shipped via carrier"));
        }
        if (status == OrderStatus.DELIVERED) {
            rec.history.add(historyEntry(OrderStatus.DELIVERED, "Delivered to recipient"));
        }
        if (status == OrderStatus.CANCELLED) {
            rec.history.add(historyEntry(OrderStatus.CANCELLED, "Cancelled by customer"));
        }
        int total = items.stream().mapToInt(i -> i.getQuantity() * i.getUnitPriceCents()).sum();
        rec.totalCents = total;
        orders.put(id, rec);
    }

    @Override
    public GetOrderStatusResponse getOrderStatus(GetOrderStatusRequest params) {
        String orderId = params.getOrderId();
        OrderRecord rec = orders.get(orderId);
        if (rec == null) {
            throw new RuntimeException("Order not found: " + orderId);
        }

        GetOrderStatusResponse resp = new GetOrderStatusResponse();
        resp.setOrderId(rec.orderId);
        resp.setStatus(rec.status);
        resp.setEstimatedDelivery(toXmlDate(LocalDate.now().plusDays(5)));

        if (params.isIncludeHistory()) {
            for (var h : rec.history) {
                resp.getHistory().add(h);
            }
        }
        return resp;
    }

    @Override
    public SubmitOrderResponse submitOrder(SubmitOrderRequest params) {
        String orderId = "ORD-" + counter.incrementAndGet();
        int total = 0;
        for (OrderLineItem it : params.getItems()) {
            total += it.getQuantity() * it.getUnitPriceCents();
        }

        OrderRecord rec = new OrderRecord();
        rec.orderId = orderId;
        rec.customerId = params.getCustomerId();
        rec.status = OrderStatus.PENDING;
        rec.priority = params.getPriority();
        rec.items = new ArrayList<>(params.getItems());
        rec.address = params.getShippingAddress();
        rec.notes = params.getNotes();
        rec.totalCents = total;
        rec.history = new ArrayList<>();
        rec.history.add(historyEntry(OrderStatus.PENDING, "Order placed"));
        orders.put(orderId, rec);

        SubmitOrderResponse resp = new SubmitOrderResponse();
        resp.setConfirmationNumber(orderId);
        resp.setTotalAmountCents(total);

        int days = switch (params.getPriority()) {
            case OVERNIGHT -> 1;
            case EXPRESS -> 3;
            default -> 7;
        };
        resp.setEstimatedDelivery(toXmlDate(LocalDate.now().plusDays(days)));
        return resp;
    }

    @Override
    public CancelOrderResponse cancelOrder(CancelOrderRequest params) {
        OrderRecord rec = orders.get(params.getOrderId());
        CancelOrderResponse resp = new CancelOrderResponse();
        if (rec == null) {
            resp.setSuccess(false);
            resp.setMessage("Order not found: " + params.getOrderId());
            return resp;
        }
        if (rec.status == OrderStatus.SHIPPED || rec.status == OrderStatus.DELIVERED) {
            resp.setSuccess(false);
            resp.setMessage("Cannot cancel order in status: " + rec.status);
            return resp;
        }
        rec.status = OrderStatus.CANCELLED;
        rec.history.add(historyEntry(OrderStatus.CANCELLED, params.getReason()));
        resp.setSuccess(true);
        resp.setMessage("Order " + params.getOrderId() + " cancelled successfully");
        return resp;
    }

    // --- helpers ---

    private OrderLineItem item(String sku, String name, int qty, int price) {
        OrderLineItem it = new OrderLineItem();
        it.setSku(sku);
        it.setName(name);
        it.setQuantity(qty);
        it.setUnitPriceCents(price);
        return it;
    }

    private Address address(String street, String city, String state, String zip, String country) {
        Address a = new Address();
        a.setStreet(street);
        a.setCity(city);
        a.setState(state);
        a.setZipCode(zip);
        a.setCountry(country);
        return a;
    }

    private HistoryEntry historyEntry(OrderStatus status, String note) {
        HistoryEntry h = new HistoryEntry();
        try {
            h.setTimestamp(DatatypeFactory.newInstance()
                .newXMLGregorianCalendar(LocalDateTime.now().toString()));
        } catch (Exception ignored) {}
        h.setStatus(status);
        h.setNote(note);
        return h;
    }

    private XMLGregorianCalendar toXmlDate(LocalDate date) {
        try {
            return DatatypeFactory.newInstance()
                .newXMLGregorianCalendar(date.toString());
        } catch (Exception e) {
            return null;
        }
    }

    private static class OrderRecord {
        String orderId;
        String customerId;
        OrderStatus status;
        Priority priority;
        List<OrderLineItem> items;
        Address address;
        String notes;
        int totalCents;
        List<HistoryEntry> history;
    }
}

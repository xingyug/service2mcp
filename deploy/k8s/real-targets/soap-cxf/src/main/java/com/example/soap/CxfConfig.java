package com.example.soap;

import com.example.order.generated.OrderPortType;
import jakarta.xml.ws.Endpoint;
import org.apache.cxf.Bus;
import org.apache.cxf.jaxws.EndpointImpl;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class CxfConfig {

    @Bean
    public Endpoint orderEndpoint(Bus bus, OrderPortType orderService) {
        EndpointImpl endpoint = new EndpointImpl(bus, orderService);
        endpoint.publish("/OrderService");
        return endpoint;
    }
}
